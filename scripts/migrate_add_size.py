import logging

from reproserver import database
from reproserver.objectstore import get_object_store


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    DBSession = database.connect()
    db = DBSession()
    object_store = get_object_store()
    for experiment in db.query(database.Experiment).all():
        meta = object_store.get_object_metadata('experiments', experiment.hash)
        experiment.size = meta['ContentLength']
    db.commit()


if __name__ == '__main__':
    main()
