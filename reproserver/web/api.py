import asyncio
from datetime import datetime, timezone
import functools
import hashlib
import logging
import tempfile
from tornado.web import HTTPError, stream_request_body

from .base import BaseHandler
from .. import database
from ..run.connector import DirectConnector


logger = logging.getLogger(__name__)


class BaseApiHandler(BaseHandler):
    def check_xsrf_cookie(self):
        pass

    def prepare(self):
        token = self.request.headers.pop('X-Reproserver-Authenticate', None)
        if token != self.application.settings['connection_token']:
            self.set_status(403)
            logger.info("Unauthenticated connector request")
            self.finish("Unauthenticated connector request")
            raise HTTPError(403)

    @property
    def connector(self):
        return DirectConnector(
            DBSession=self.application.DBSession,
            object_store=self.application.object_store,
        )


def parse_run_id(wrapped):
    @functools.wraps(wrapped)
    def wrapper(self, run_id, *args):
        try:
            run_id = int(run_id)
        except (ValueError, OverflowError):
            return self.send_error_json(400, "Invalid run ID")
        return wrapped(self, run_id, *args)

    return wrapper


class InitRunGetInfo(BaseApiHandler):
    @parse_run_id
    async def post(self, run_id):
        run_info = await self.connector.init_run_get_info(run_id)

        # Get signed download link for bundle
        run_info['experiment_url'] = self.connector.get_bundle_link(run_info)

        # Get signed download links for all input files
        run_info = self.connector.get_input_links(run_info)

        return await self.send_json(run_info)


class RunStarted(BaseApiHandler):
    @parse_run_id
    async def post(self, run_id):
        await self.connector.run_started(run_id)


class RunSetProgress(BaseApiHandler):
    @parse_run_id
    async def post(self, run_id):
        body = self.get_json()
        try:
            percent = body['percent']
            text = body['text']
            if (
                not isinstance(percent, int)
                or not isinstance(text, str)
            ):
                raise KeyError
        except KeyError:
            return await self.send_error_json(
                400,
                "Expected JSON object with 'percent' and 'text' keys",
            )

        await self.connector.run_progress(run_id, percent, text)


class RunDone(BaseApiHandler):
    @parse_run_id
    async def post(self, run_id):
        await self.connector.run_done(run_id)


class RunFailed(BaseApiHandler):
    @parse_run_id
    async def post(self, run_id):
        body = self.get_json()
        try:
            error = body['error']
            if not isinstance(error, str):
                raise KeyError
        except KeyError:
            return await self.send_error_json(
                400,
                "Expected JSON object with 'error' key",
            )

        await self.connector.run_failed(run_id, error)


@stream_request_body
class UploadOutput(BaseApiHandler):
    # FIXME: Round-trip to disk to compute hash, not ideal
    def prepare(self):
        self.temp_file = tempfile.NamedTemporaryFile()
        self.hasher = hashlib.sha256()

    def on_finish(self):
        self.temp_file.close()
        self.temp_file = None

    def data_received(self, chunk):
        self.temp_file.write(chunk)
        self.hasher.update(chunk)

    @parse_run_id
    def put(self, run_id, output_name):
        self.temp_file.flush()

        return asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.connector.upload_output_file_blocking(
                run_id,
                output_name,
                self.temp_file,
                digest=self.hasher.hexdigest(),
            ),
        )


class Log(BaseApiHandler):
    @parse_run_id
    def post(self, run_id):
        obj = self.get_json()
        for line in obj['lines']:
            self.db.add(database.RunLogLine(
                run_id=run_id,
                line=line['msg'],
                timestamp=(
                    datetime
                    .fromisoformat(line['time'])
                    .replace(tzinfo=timezone.utc)
                ),
            ))
        self.db.commit()
        self.set_status(204)
        return self.finish()
