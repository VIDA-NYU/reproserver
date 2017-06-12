import boto3
from botocore.client import Config
import logging
import os


def get_object_store(endpoint_url=None):
    logging.info("Logging in to S3")
    if endpoint_url is None:
        endpoint_url = os.environ.get('S3_URL') or None
    return boto3.resource('s3', endpoint_url=endpoint_url,
                          aws_access_key_id=os.environ['S3_KEY'],
                          aws_secret_access_key=os.environ['S3_SECRET'],
                          region_name='us-east-1',
                          config=Config(signature_version='s3v4'))
