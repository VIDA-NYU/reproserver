import boto3
from botocore.client import Config
import logging
import os


def get_object_store(endpoint_url=None):
    logging.info("Logging in to S3")
    if endpoint_url is None:
        endpoint_url = os.environ.get('S3_URL') or None
    bucket_prefix = os.environ['S3_BUCKET_PREFIX']
    return ObjectStore(endpoint_url, bucket_prefix)


class ObjectStore(object):
    def __init__(self, endpoint_url, bucket_prefix):
        self.s3 = boto3.resource('s3', endpoint_url=endpoint_url,
                                 aws_access_key_id=os.environ['S3_KEY'],
                                 aws_secret_access_key=os.environ['S3_SECRET'],
                                 region_name='us-east-1',
                                 config=Config(signature_version='s3v4'))
        self.bucket_prefix = bucket_prefix

    def bucket_name(self, name):
        if name not in ('experiments', 'inputs', 'outputs'):
            raise ValueError("Invalid bucket name %s" % name)

        name = '%s-%s-%s' % ('reproserver', self.bucket_prefix, name)
        return name

    def bucket(self, name):
        return self.s3.Bucket(self.bucket_name(name))

    def download_file(self, bucket, objectname, filename):
        self.bucket(bucket).download_file(objectname, filename)

    def upload_fileobj(self, bucket, objectname, fileobj):
        self.s3.Object(self.bucket_name(bucket), objectname).put(Body=fileobj)

    def upload_file(self, bucket, objectname, filename):
        self.s3.meta.client.upload_file(filename,
                                        self.bucket_name(bucket), objectname)

    def create_buckets(self):
        buckets = set(bucket.name for bucket in self.s3.buckets.all())
        missing = []
        for name in ('experiments', 'inputs', 'outputs'):
            name = self.bucket_name(name)
            if name not in buckets:
                missing.append(name)

        if missing:
            logging.info("The buckets don't seem to exist; creating %s",
                         ", ".join(missing))
            for name in missing:
                self.s3.create_bucket(Bucket=name)

    def presigned_serve_url(self, bucket, objectname, filename, mime=None):
        return self.s3.meta.client.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': self.bucket_name(bucket),
                    'Key': objectname,
                    'ResponseContentType': mime or 'application/octet-stream',
                    'ResponseContentDisposition': 'inline; filename=%s' %
                                                  filename})
