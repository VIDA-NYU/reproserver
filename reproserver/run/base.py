import asyncio
import logging
import prometheus_client

from ..utils import background_future, prom_incremented


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

    async def run(self, run_id):
        """Called to trigger a run.
        """
        logger.info("Run request received: %r", run_id)

        with prom_incremented(PROM_RUNS):
            run_info = await self.connector.init_run_get_info(run_id)

            try:
                await asyncio.ensure_future(self.run_inner(run_info))
            except Exception as e:
                logger.exception("Error processing run!")
                logger.warning("Got error: %s", str(e))
                background_future(self.connector.run_failed(run_id, str(e)))

    async def run_inner(self, run_info):
        """Executes the experiment. Overridable in subclasses.
        """
        raise NotImplementedError
