import asyncio
from datetime import datetime
import hashlib
import tempfile
from tornado.web import stream_request_body

from .base import BaseHandler
from .. import database
from ..run.connector import DirectConnector


class BaseApiHandler(BaseHandler):
    def check_xsrf_cookie(self):
        pass

    @property
    def connector(self):
        return DirectConnector(
            DBSession=self.application.DBSession,
            object_store=self.application.object_store,
        )


class InitRunGetInfo(BaseApiHandler):
    async def post(self, run_id):
        try:
            run_id = int(run_id)
        except (ValueError, OverflowError):
            return await self.send_error_json(400, "Invalid run ID")

        run_info = await self.connector.init_run_get_info(run_id)

        # Get signed download link for bundle
        run_info['experiment_url'] = self.connector.get_bundle_link(run_info)

        # Get signed download links for all input files
        run_info = self.connector.get_input_links(run_info)

        return await self.send_json(run_info)


class RunStarted(BaseApiHandler):
    async def post(self, run_id):
        try:
            run_id = int(run_id)
        except (ValueError, OverflowError):
            return await self.send_error_json(400, "Invalid run ID")

        await self.connector.run_started(run_id)


class RunDone(BaseApiHandler):
    async def post(self, run_id):
        try:
            run_id = int(run_id)
        except (ValueError, OverflowError):
            return await self.send_error_json(400, "Invalid run ID")

        await self.connector.run_done(run_id)


class RunFailed(BaseApiHandler):
    async def post(self, run_id):
        try:
            run_id = int(run_id)
        except (ValueError, OverflowError):
            return await self.send_error_json(400, "Invalid run ID")

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

    def put(self, run_id, output_name):
        try:
            run_id = int(run_id)
        except (ValueError, OverflowError):
            return self.send_error_json(400, "Invalid run ID")

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
    def post(self, run_id):
        obj = self.get_json()
        for line in obj['lines']:
            self.db.add(database.RunLogLine(
                run_id=run_id,
                line=line['msg'],
                timestamp=datetime.fromisoformat(line['time']),
            ))
        self.db.commit()
        self.set_status(204)
        return self.finish()
