from common import database
from common import TaskQueues, get_object_store
from common.utils import setup_logging, shell_escape
from hashlib import sha256
import logging
import os
import shutil
from sqlalchemy.orm import joinedload
from sqlalchemy.sql import functions
import subprocess
import tempfile


SQLSession = None
object_store = None


# IP as understood by Docker daemon, not this container
DOCKER_REGISTRY = os.environ.get('REGISTRY', 'localhost:5000')


def run_cmd_and_log(session, run_id, cmd):
    proc = subprocess.Popen(cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    proc.stdin.close()
    for line in iter(proc.stdout.readline, b''):
        line = line.decode('utf-8', 'replace')
        logging.info("> %s", line)
        session.add(database.RunLogLine(
            run_id=run_id,
            line=line.rstrip()))
        session.commit()
    return proc.wait()


def run_request(channel, method, _properties, body):
    """Process a run task.

    Lookup a run in the database, get the input files from S3, then do the run
    from the Docker image, upload the log and the output files.
    """
    body = body.decode('ascii')
    logging.info("Run request received: %r", body)

    # Look up the run in the database
    session = SQLSession()
    exp = joinedload(database.Run.experiment)
    run = (session.query(database.Run)
           .options(joinedload(database.Run.parameter_values),
                    joinedload(database.Run.input_files),
                    exp.joinedload(database.Experiment.parameters),
                    exp.joinedload(database.Experiment.paths))
           .get(int(body)))
    if not run:
        logging.error("Got a run request but couldn't get the run from the "
                      "database (body=%r)", body)
        # ACK anyway
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Update status in database
    if run.started:
        logging.warning("Starting run which has already been started")
    else:
        run.started = functions.now()
        session.commit()

    # Remove previous info
    run.log[:] = []
    run.output_files[:] = []

    def set_error(msg):
        logging.warning("Got error: %s", msg)
        run.done = functions.now()
        session.add(database.RunLogLine(run_id=run.id, line=msg))
        session.commit()
        channel.basic_ack(delivery_tag=method.delivery_tag)

    if run.experiment.status != database.Status.BUILT:
        return set_error("Experiment to run is not BUILT")

    # Make build directory
    directory = tempfile.mkdtemp('build_%s' % run.experiment_hash)

    container = None
    fq_image_name = '%s/%s' % (DOCKER_REGISTRY, run.experiment.docker_image)

    try:
        # Get list of parameters
        params = {}
        params_unset = set()
        for param in run.experiment.parameters:
            if not param.optional:
                params_unset.add(param.name)
            params[param.name] = param.default

        # Get parameter values
        for param in run.parameter_values:
            if param.name in params:
                logging.info("Param: %s=%r", param.name, param.value)
                params[param.name] = param.value
                params_unset.discard(param.name)
            else:
                return set_error("Got parameter value for parameter %s which "
                                 "does not exist" % param.name)

        if params_unset:
            return set_error("Missing value for parameters: %s" %
                             ", ".join(params_unset))

        # Get paths
        paths = {}
        for path in run.experiment.paths:
            paths[path.name] = path.path

        # Get input files
        inputs = []
        for input_file in run.input_files:
            if input_file.name not in paths:
                return set_error("Got an unknown input file %s" %
                                 input_file.name)
            inputs.append((input_file,
                           paths[input_file.name]))
        logging.info("Using %d input files: %s", len(inputs),
                     ", ".join(f.name for f, p in inputs))

        # Create container
        container = 'run_%s' % body
        logging.info("Creating container %s with image %s",
                     container, run.experiment.docker_image)
        # Turn parameters into a command-line
        cmdline = []
        for k, v in params.items():
            if k.startswith('cmdline_'):
                i = k[8:]
                cmdline.extend(['cmd', v, 'run', i])
        cmdline = ['docker', 'create', '-i', '--name', container,
                   '--', fq_image_name] + cmdline
        logging.info('$ %s', ' '.join(shell_escape(a) for a in cmdline))
        subprocess.check_call(cmdline)

        for input_file, path in inputs:
            local_path = os.path.join(directory, 'input_%s' % input_file.hash)

            # Download file from S3
            logging.info("Downloading input file: %s, %s, %d bytes",
                         input_file.name, input_file.hash, input_file.size)
            object_store.download_file('inputs', input_file.hash, local_path)

            # Put file in container
            logging.info("Copying file to container")
            subprocess.check_call(['docker', 'cp', '--',
                                   local_path,
                                   '%s:%s' % (container, path)])

            # Remove local file
            os.remove(local_path)

        # Start container using parameters
        logging.info("Starting container")
        try:
            ret = run_cmd_and_log(session, run.id,
                                  ['docker', 'start', '-ai', '--', container])
        except IOError:
            return set_error("Got IOError running experiment")
        if ret != 0:
            return set_error("Error: Docker returned %d" % ret)
        run.done = functions.now()

        # Get output files
        for path in run.experiment.paths:
            if path.is_output:
                local_path = os.path.join(directory, 'output_%s' % path.name)

                # Copy file out of container
                logging.info("Getting output file %s", path.name)
                ret = subprocess.call(['docker', 'cp', '--',
                                       '%s:%s' % (container, path.path),
                                       local_path])
                if ret != 0:
                    logging.warning("Couldn't get output %s", path.name)
                    session.add(database.RunLogLine(
                        run_id=run.id,
                        line="Couldn't get output %s" % path.name))
                    continue

                with open(local_path, 'rb') as fp:
                    # Hash it
                    hasher = sha256()
                    chunk = fp.read(4096)
                    while chunk:
                        hasher.update(chunk)
                        chunk = fp.read(4096)
                    filehash = hasher.hexdigest()

                    # Rewind it
                    filesize = fp.tell()
                    fp.seek(0, 0)

                    # Upload file to S3
                    logging.info("Uploading file, size: %d bytes" % filesize)
                    object_store.upload_fileobj('outputs', filehash, fp)

                # Add OutputFile to database
                run.output_files.append(
                    database.OutputFile(hash=filehash, name=path.name,
                                        size=filesize))

                # Remove local file
                os.remove(local_path)

        # ACK
        session.commit()
        channel.basic_ack(delivery_tag=method.delivery_tag)
        logging.info("Done!")
    except Exception:
        logging.exception("Error processing run!")
        if True:
            set_error("Internal error!")
        else:
            # Set database status back to QUEUED
            run.status = database.Status.QUEUED
            session.commit()
            # NACK the task in RabbitMQ
            channel.basic_nack(delivery_tag=method.delivery_tag)
    finally:
        # Remove container if created
        if container is not None:
            subprocess.call(['docker', 'rm', '-f', '--', container])
        # Remove image
        subprocess.call(['docker', 'rmi', '--', fq_image_name])
        # Remove build directory
        shutil.rmtree(directory)


def main():
    setup_logging('REPROSERVER-RUNNER')

    # SQL database
    global SQLSession
    engine, SQLSession = database.connect()

    # AMQP
    tasks = TaskQueues()

    # Object storage
    global object_store
    object_store = get_object_store()

    logging.info("Ready, listening for requests")
    tasks.consume_run_tasks(run_request)
