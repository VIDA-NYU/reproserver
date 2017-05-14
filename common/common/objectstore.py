import boto3
import logging
import os


def get_object_store():
    logging.info("Logging in to S3")
    return boto3.resource('s3', endpoint_url=os.environ.get('S3_URL'),
                          aws_access_key_id=os.environ['S3_KEY'],
                          aws_secret_access_key=os.environ['S3_SECRET'])
