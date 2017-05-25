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
    s3 = get_object_store()

    it = iter(s3.buckets.all())
    try:
        next(it)
    except StopIteration:
        logging.info("The buckets don't seem to exist; creating")
        for name in ['experiments', 'inputs', 'outputs']:
            s3.create_bucket(Bucket=name)

    # Download the examples and add them
    examples = [
        ('bechdel.rpz', 'https://drive.google.com/uc?export=download&id=0B3ucP'
                        'z7GSthBRjVqZ2xFeGpITTQ',
         "Attempt to replicate the findings from a FiveThirtyEight article "
         "examining gender bias in the movie business"),
        ('digits_sklearn_opencv.rpz', 'https://drive.google.com/uc?export=down'
                                      'load&id=0B3ucPz7GSthBZm5Wa1lxNWZFVTA',
         "Recognizing the value of hand-written digits using OpenCV and "
         "scikit-learn"),
        ('bash-count.rpz', 'https://drive.google.com/uc?export=download&id=0B3'
                           'ucPz7GSthBeDFuMkRXLUlzem8',
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
                    continue

                    # Rewind it
                downloaded_file.seek(0, 0)

                # Insert it on S3
                s3.Object('experiments', filehash).put(Body=downloaded_file)
                logging.info("Inserted file in storage")

                # Insert it in database
                experiment = database.Experiment(hash=filehash)
                session.add(experiment)
                upload = database.Upload(experiment=experiment,
                                         filename=name,
                                         submitted_ip='127.0.0.1')
                session.add(upload)
                session.add(database.Example(upload=upload,
                                             description=description))
                session.commit()
                logging.info("Inserted file in database")
        finally:
            # Remove
            os.remove(local_path)
