import asyncio
import boto3
from botocore.client import Config
import botocore.exceptions
import io
import logging
import os
from tornado import httpclient


logger = logging.getLogger(__name__)


def get_object_store():
    logger.info("Logging in to S3")
    return ObjectStore(
        os.environ['S3_URL'],
        os.environ['S3_CLIENT_URL'],
        os.environ['S3_BUCKET_PREFIX'],
    )


class ObjectStore(object):
    def __init__(self, endpoint_url, client_endpoint_url, bucket_prefix):
        self.s3 = boto3.resource(
            's3', endpoint_url=endpoint_url,
            aws_access_key_id=os.environ['S3_KEY'],
            aws_secret_access_key=os.environ['S3_SECRET'],
            region_name='us-east-1',
            config=Config(signature_version='s3v4'),
        )
        self.s3_client = boto3.resource(
            's3', endpoint_url=client_endpoint_url,
            aws_access_key_id=os.environ['S3_KEY'],
            aws_secret_access_key=os.environ['S3_SECRET'],
            region_name='us-east-1',
            config=Config(signature_version='s3v4'),
        )
        self.bucket_prefix = bucket_prefix

    async def check(self):
        client = httpclient.AsyncHTTPClient()
        try:
            res = await client.fetch(
                os.environ['S3_URL'],
                raise_error=False,
                request_timeout=2,
            )
        except (OSError, httpclient.HTTPError):
            return "S3 unavailable"
        if not (200 <= res.code <= 500):
            return "S3 failing"
        return None

    def bucket_name(self, name):
        if name not in ('experiments', 'inputs', 'outputs'):
            raise ValueError("Invalid bucket name %s" % name)

        name = '%s%s' % (self.bucket_prefix, name)
        return name

    def bucket(self, name):
        return self.s3.Bucket(self.bucket_name(name))

    def download_file(self, bucket, objectname, filename):
        self.bucket(bucket).download_file(objectname, filename)

    def upload_fileobj(self, bucket, objectname, fileobj):
        # s3.Object(...).put(...) and s3.meta.client.upload_file(...) do
        # multipart uploads which don't work on GCP
        self.s3.meta.client.put_object(
            Bucket=self.bucket_name(bucket),
            Key=objectname,
            Body=fileobj,
        )

    def upload_file(self, bucket, objectname, filename):
        with open(filename, 'rb') as fileobj:
            self.upload_fileobj(bucket, objectname, fileobj)

    def upload_file_async(self, bucket, objectname, filename):
        return asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.upload_file(
                bucket, objectname, filename,
            ),
        )

    def upload_bytes(self, bucket, objectname, bytestr):
        self.upload_fileobj(bucket, objectname, io.BytesIO(bytestr))

    def upload_bytes_async(self, bucket, objectname, bytestr):
        return asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.upload_bytes(
                bucket, objectname, bytestr,
            ),
        )

    def create_buckets(self):
        missing = []
        for name in ('experiments', 'inputs', 'outputs'):
            name = self.bucket_name(name)
            try:
                self.s3.meta.client.head_bucket(Bucket=name)
            except botocore.exceptions.ClientError:
                missing.append(name)

        if missing:
            logger.info("The buckets don't seem to exist; creating %s",
                        ", ".join(missing))
            for name in missing:
                self.s3.create_bucket(Bucket=name)

    def get_object_metadata(self, bucket, objectname):
        return self.s3.meta.client.head_object(
            Bucket=self.bucket_name(bucket),
            Key=objectname,
        )

    def presigned_internal_url(self, bucket, objectname):
        return self.s3.meta.client.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': self.bucket_name(bucket),
                    'Key': objectname},
        )

    def presigned_serve_url(self, bucket, objectname, filename, mime=None):
        return self.s3_client.meta.client.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': self.bucket_name(bucket),
                    'Key': objectname,
                    'ResponseContentType': mime or 'application/octet-stream',
                    'ResponseContentDisposition': 'inline; filename=%s' %
                                                  filename},
        )
