import asyncio
import logging
import prometheus_client
import subprocess


logger = logging.getLogger(__name__)


PROM_RUNS = prometheus_client.Gauge(
    'current_runs',
    "Runs currently happening",
)


def run_cmd_and_log(session, run_id, cmd, to_db):
    proc = subprocess.Popen(cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    proc.stdin.close()
    for line in iter(proc.stdout.readline, b''):
        line = line.decode('utf-8', 'replace')
        line = line.rstrip()
        logger.info("> %s", line)
        if to_db is not None:
            session.add(to_db(line))
            session.commit()
    return proc.wait()


class BaseRunner(object):
    """Base class for runners.

    This is in charge of taking an experiment and running it, building it first
    if necessary.
    """
    def __init__(self, *, DBSession, object_store):
        self.DBSession = DBSession
        self.object_store = object_store

    def _run_callback(self, run_id):
        """Provides a callback that marks the Run as completed/failed.
        """
        def callback(future):
            try:
                future.result()
                logger.info("Run %d successful", run_id)
            except Exception:
                logger.exception("Exception in run %d", run_id)
            PROM_RUNS.dec()

        return callback

    def run(self, run_id):
        """Called to trigger a run. Should not block.
        """
        # Default implementation calls run_sync() in a thread; either method
        # can be overloaded
        future = asyncio.get_event_loop().run_in_executor(
            None,
            self.run_sync,
            run_id,
        )
        future.add_done_callback(self._run_callback(run_id))
        PROM_RUNS.inc()
        return future

    def run_sync(self, run_id):
        """Executes the experiment. Overridable in subclasses.

        You don't need to implement it if you implement `run_async()`.
        """
        raise NotImplementedError
