import asyncio
import logging
import tempfile

from reproserver import database
from reproserver.extensions import process_uploaded_rpz
from reproserver.objectstore import get_object_store
from reproserver.rpz_metadata import get_metadata


logger = logging.getLogger('reprocess_experiments')


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    DBSession = database.connect()
    db = DBSession()
    object_store = get_object_store()

    experiments = db.query(database.Experiment).all()

    for i, experiment in enumerate(experiments):
        logger.info("Experiments re-processed: %d/%d", i, len(experiments))

        with tempfile.NamedTemporaryFile(
            prefix='reprocess_',
            suffix='.rpz',
        ) as tmp:
            # Download RPZ
            object_store.download_file(
                'experiments',
                experiment.hash,
                tmp.name,
            )

            # Update metadata
            info, experiment.info = get_metadata(tmp.name)

            # Remove existing extensions
            db.execute(
                database.Extension.__table__.delete()
                .where(database.Extension.experiment_hash == experiment.hash)
            )

            # Update extensions
            await process_uploaded_rpz(object_store, db, experiment, tmp.name)

    db.commit()
    logger.info("Done")


if __name__ == '__main__':
    asyncio.run(main())
