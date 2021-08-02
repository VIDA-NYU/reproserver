import asyncio
import logging
import prometheus_client

from ..utils import background_future


logger = logging.getLogger(__name__)


PROM_RUNS = prometheus_client.Gauge(
    'current_runs',
    "Runs currently happening",
)


class BaseRunner(object):
    """Base class for runners.

    This is in charge of taking an experiment and running it, building it first
    if necessary.
    """
    def __init__(self, connector):
        self.loop = asyncio.get_event_loop()
        self.connector = connector

    def _run_callback(self, run_id):
        """Provides a callback that marks the Run as completed/failed.
        """
        def callback(future):
            try:
                future.result()
            except Exception as e:
                logger.exception("Error processing run!")
                logger.warning("Got error: %s", str(e))
                background_future(self.connector.run_failed(run_id, str(e)))
            else:
                logger.info("Run %d successful", run_id)

            PROM_RUNS.dec()

        return callback

    async def run(self, run_id):
        """Called to trigger a run. Should not block.
        """
        logger.info("Run request received: %r", run_id)

        PROM_RUNS.inc()

        run_info = await self.connector.init_run_get_info(run_id)

        future = self.run_async(run_info)
        future = asyncio.ensure_future(future)
        future.add_done_callback(self._run_callback(run_id))
        return await future

    async def run_async(self, run_info):
        """Executes the experiment. Overridable in subclasses.

        The default implementation just calls `run_sync()` in a thread.
        """
        # Default implementation calls run_sync() in a thread; either method
        # can be overloaded
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.run_sync(run_info),
        )

    def run_sync(self, run_info):
        """Executes the experiment. Overridable in subclasses.

        You don't need to implement it if you implement `run_async()`.
        """
        raise NotImplementedError
