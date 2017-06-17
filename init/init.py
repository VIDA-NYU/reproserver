from common import database
from common import TaskQueues, get_object_store
from common.utils import setup_logging
from hashlib import sha256
import logging
import os
import requests


def download_file_retry(url, dest):
    attempt = 0
    while True:
        attempt += 1
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            try:
                with open(dest, 'wb') as f:
                    for chunk in response.iter_content(4096):
                        f.write(chunk)
                    response.close()
            except Exception as e:
                try:
                    os.remove(dest)
                except OSError:
                    pass
                raise e
            return
        except requests.RequestException as e:
            if attempt == 2:
                raise e
            logging.warning("Download %s: retrying after error: %s", url, e)


def main():
    setup_logging('REPROSERVER-INIT')

    # SQL database
    engine, SQLSession = database.connect()

    if not engine.dialect.has_table(engine.connect(), 'experiments'):
        logging.warning("The tables don't seem to exist; creating")
        from common.database import Base

        Base.metadata.create_all(bind=engine)

    # Object storage
    object_store = get_object_store()

    object_store.create_buckets()

    # Download the examples and add them
    examples = [
        ('bechdel.rpz', 'http://reproserver-examples.s3-website-us-east-1.amaz'
                        'onaws.com/bechdel.rpz',
         "Attempt to replicate the findings from a FiveThirtyEight article "
         "examining gender bias in the movie business"),
        ('digits_sklearn_opencv.rpz', 'http://reproserver-examples.s3-website-'
                                      'us-east-1.amazonaws.com/digits_sklearn_'
                                      'opencv.rpz',
         "Recognizing the value of hand-written digits using OpenCV and "
         "scikit-learn"),
        ('bash-count.rpz', 'http://reproserver-examples.s3-website-us-east-1.a'
                           'mazonaws.com/bash-count.rpz',
         "Simple bash script counting the lines in a file"),
    ]

    for name, url, description in examples:
        # Download
        logging.info("Downloading %s", name)
        local_path = os.path.join('/tmp', name)
        download_file_retry(url, local_path)

        try:
            with open(local_path, 'rb') as downloaded_file:
                # Hash it
                hasher = sha256()
                chunk = downloaded_file.read(4096)
                while chunk:
                    hasher.update(chunk)
                    chunk = downloaded_file.read(4096)
                filehash = hasher.hexdigest()

                # Check for existence of experiment
                session = SQLSession()
                experiment = session.query(database.Experiment).get(filehash)
                if experiment:
                    logging.info("File %s exists", name)
                else:
                    # Rewind it
                    downloaded_file.seek(0, 0)

                    # Insert it on S3
                    object_store.upload_fileobj('experiments', filehash,
                                                downloaded_file)
                    logging.info("Inserted file in storage")

                    # Insert it in database
                    experiment = database.Experiment(hash=filehash)
                    session.add(experiment)

                # Check for existence of example
                examples = session.query(database.Example).filter(
                    database.Example.upload.has(experiment_hash=filehash))
                if examples.count():
                    logging.info("Example for %s exists", name)
                else:
                    upload = database.Upload(experiment=experiment,
                                             filename=name,
                                             submitted_ip='127.0.0.1')
                    session.add(upload)
                    session.add(database.Example(upload=upload,
                                                 description=description))
                    logging.info("Inserted file in database")
                session.commit()
        finally:
            # Remove
            os.remove(local_path)
