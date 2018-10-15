from common import database
from common import TaskQueues, get_object_store
from common.utils import setup_logging, shell_escape
import json
import logging
import os
import shutil
import subprocess
import tempfile


SQLSession = None
object_store = None


# IP as understood by Docker daemon, not this container
DOCKER_REGISTRY = os.environ.get('REGISTRY', 'localhost:5000')


def run_cmd_and_log(session, experiment_hash, cmd):
    session.add(database.BuildLogLine(
        experiment_hash=experiment_hash,
        line=' '.join(cmd)))
    session.commit()
    proc = subprocess.Popen(cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    proc.stdin.close()
    try:
        for line in iter(proc.stdout.readline, b''):
            line = line.decode('utf-8', 'replace')
            logging.info("> %s", line)
            session.add(database.BuildLogLine(
                experiment_hash=experiment_hash,
                line=line.rstrip()))
            session.commit()
        ret = proc.wait()
        if ret != 0:
            return "Process returned %d" % proc.returncode
    except IOError:
        return "Got IOError"


def build_request(channel, method, _properties, body):
    """Process a build task.

    Lookup the experiment in the database, and the file on S3. Then, do the
    build, upload the log, and fill in the parameters in the database.
    """
    body = body.decode('ascii')
    logging.info("Build request received: %r", body)

    # Look up the experiment in the database
    session = SQLSession()
    experiment = session.query(database.Experiment).get(body)
    if not experiment:
        logging.error("Got a build request but couldn't get the experiment "
                      "from the database (body=%r)", body)
        # ACK anyway
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Update status in database
    if experiment.status != database.Status.QUEUED:
        logging.warning("Building experiment which has status %r",
                        experiment.status)
    experiment.status = database.Status.BUILDING
    experiment.docker_image = None
    experiment.parameters[:] = []
    experiment.paths[:] = []
    experiment.log[:] = []
    session.commit()
    logging.info("Set status to BUILDING")

    # Make build directory
    directory = tempfile.mkdtemp('build_%s' % experiment.hash)

    def set_error(msg):
        logging.warning("Got error: %s", msg)
        experiment.status = database.Status.ERROR
        session.add(database.BuildLogLine(experiment_hash=experiment.hash,
                                          line=msg))
        session.commit()
        channel.basic_ack(delivery_tag=method.delivery_tag)

    try:
        # Get experiment file
        logging.info("Downloading file...")
        local_path = os.path.join(directory, 'experiment.rpz')
        build_dir = os.path.join(directory, 'build_dir')
        object_store.download_file('experiments', experiment.hash, local_path)
        logging.info("Got file, %d bytes", os.stat(local_path).st_size)

        # Get metadata
        info_proc = subprocess.Popen(['reprounzip', 'info', '--json',
                                      local_path],
                                     stdout=subprocess.PIPE)
        info_stdout, _ = info_proc.communicate()
        if info_proc.wait() != 0:
            return set_error("Error getting info from package")
        info = json.loads(info_stdout.decode('utf-8'))
        logging.info("Got metadata, %d runs", len(info['runs']))

        # Remove previous build log
        experiment.log[:] = []
        session.commit()

        # Build the experiment
        image_name = 'rpuz_exp_%s' % experiment.hash
        fq_image_name = '%s/%s' % (DOCKER_REGISTRY, image_name)
        logging.info("Building image %s...", fq_image_name)
        err = run_cmd_and_log(session, experiment.hash,
                              ['reprounzip', '-v', 'docker', 'setup',
                               '--image-name', fq_image_name,
                               local_path, build_dir])
        if err is not None:
            return set_error(err)

        session.add(database.BuildLogLine(experiment_hash=experiment.hash,
                                          line="Build successful"))
        experiment.docker_image = image_name
        session.commit()
        logging.info("Build over, pushing image")

        # Push image to Docker repository
        subprocess.check_call(['docker', 'push', fq_image_name])
        logging.info("Push complete, finishing up")

        # Add parameters
        # Command-line of each run
        for i, run in enumerate(info['runs']):
            cmdline = ' '.join(shell_escape(a) for a in run['argv'])
            session.add(database.Parameter(
                experiment_hash=experiment.hash,
                name="cmdline_%d" % i, optional=False, default=cmdline,
                description="Command-line for step %s" % run['id']))
        # Input/output files
        for name, iofile in info.get('inputs_outputs', ()).items():
            path = iofile['path']

            # It's an input if it's read before it is written
            if iofile['read_runs'] and iofile['write_runs']:
                first_write = min(iofile['write_runs'])
                first_read = min(iofile['read_runs'])
                is_input = first_read <= first_write
            else:
                is_input = bool(iofile['read_runs'])

            # It's an output if it's ever written
            is_output = bool(iofile['write_runs'])

            session.add(database.Path(experiment_hash=experiment.hash,
                                      is_input=is_input,
                                      is_output=is_output,
                                      name=name,
                                      path=path))

        # Set status
        experiment.status = database.Status.BUILT
        # ACK
        session.commit()
        channel.basic_ack(delivery_tag=method.delivery_tag)
        logging.info("Done!")
    except Exception:
        logging.exception("Error processing build!")
        if True:
            set_error("Internal error!")
        else:
            # Set database status back to QUEUED
            experiment.status = database.Status.QUEUED
            session.commit()
            # NACK the task in RabbitMQ
            channel.basic_nack(delivery_tag=method.delivery_tag)
    finally:
        # Remove build directory
        shutil.rmtree(directory)


def main():
    setup_logging('REPROSERVER-BUILDER')

    # SQL database
    global SQLSession
    engine, SQLSession = database.connect()

    # AMQP
    tasks = TaskQueues()

    # Object storage
    global object_store
    object_store = get_object_store()

    # Wait for tasks
    logging.info("Ready, listening for requests")
    tasks.consume_build_tasks(build_request)
