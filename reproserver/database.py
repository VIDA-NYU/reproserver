from base64 import b64decode, b64encode
from datetime import datetime
import logging
import os
from sqlalchemy import Column, ForeignKey, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.types import Boolean, DateTime, Integer, String, Text
import sys
import time

from .shortid import ShortIDs


logger = logging.getLogger(__name__)


Base = declarative_base()


class Experiment(Base):
    """Experiments available on the server.

    Those match experiment files that were uploaded, whether or not an image
    has been built.

    Note that no filename is here, since the file might have been uploaded
    multiple times with different names.
    """
    __tablename__ = 'experiments'

    hash = Column(String(64), primary_key=True)
    last_access = Column(DateTime, nullable=False,
                         default=lambda: datetime.utcnow())
    info = Column(Text, nullable=False)

    extensions = relationship('Extension', back_populates='experiment')
    uploads = relationship('Upload', back_populates='experiment')
    runs = relationship('Run', back_populates='experiment')
    parameters = relationship('Parameter', back_populates='experiment')
    paths = relationship('Path', back_populates='experiment')

    def __repr__(self):
        return "<Experiment hash=%r, docker_image=%r>" % (
            self.hash,
            self.docker_image)


class Extension(Base):
    """An extension discovered and processed.
    """
    __tablename__ = 'extensions'

    experiment_hash = Column(
        String(64),
        ForeignKey('experiments.hash', ondelete='CASCADE'),
        primary_key=True,
        index=True,
    )
    experiment = relationship('Experiment', uselist=False,
                              back_populates='extensions')
    name = Column(String(64), primary_key=True)
    data = Column(Text)


class Upload(Base):
    """An upload of an experiment.

    There can be multiple uploads for the same experiment, each of them
    associated with a different uploader and filename.

    This is not used by the application, but might be important for accounting
    purposes.
    """
    __tablename__ = 'uploads'

    id = Column(Integer, primary_key=True)
    filename = Column(Text, nullable=False)
    experiment_hash = Column(String(64), ForeignKey('experiments.hash',
                                                    ondelete='CASCADE'))
    experiment = relationship('Experiment', uselist=False,
                              back_populates='uploads')
    submitted_ip = Column(Text, nullable=True)
    repository_key = Column(Text, nullable=True, index=True)
    timestamp = Column(DateTime, nullable=False,
                       default=lambda: datetime.utcnow())

    @property
    def short_id(self):
        return upload_short_ids.encode(self.id)

    @staticmethod
    def decode_id(short_id):
        return upload_short_ids.decode(short_id)

    def __repr__(self):
        return ("<Upload id=%d, experiment_hash=%r, filename=%r, "
                "submitted_ip=%r, timestamp=%r>") % (
            self.id, self.experiment_hash, self.filename,
            self.submitted_ip, self.timestamp)


class Parameter(Base):
    """An experiment parameter.

    Those are extracted from the package metadata on upload, and displayed to
    the user when running the experiment.
    """
    __tablename__ = 'parameters'

    id = Column(Integer, primary_key=True)
    experiment_hash = Column(String(64), ForeignKey('experiments.hash',
                                                    ondelete='CASCADE'))
    experiment = relationship('Experiment', uselist=False,
                              back_populates='parameters')
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    optional = Column(Boolean, nullable=False)
    default = Column(Text, nullable=True)

    def __repr__(self):
        return ("<Parameter id=%d, experiment_hash=%r, name=%r, optional=%r, "
                "default=%r") % (
            self.id, self.experiment_hash, self.name, self.optional,
            self.default)


class Path(Base):
    """Path to an input/output file in the experiment.
    """
    __tablename__ = 'paths'

    id = Column(Integer, primary_key=True)
    experiment_hash = Column(String(64), ForeignKey('experiments.hash',
                                                    ondelete='CASCADE'))
    experiment = relationship('Experiment', uselist=False,
                              back_populates='paths')
    is_input = Column(Boolean, nullable=False)
    is_output = Column(Boolean, nullable=False)
    name = Column(Text, nullable=False)
    path = Column(Text, nullable=False)

    def __repr__(self):
        if self.is_input and self.is_output:
            descr = "input+output"
        elif self.is_input:
            descr = "input"
        elif self.is_output:
            descr = "output"
        else:
            descr = "(NO FLAG)"
        return "<Path id=%d, experiment_hash=%r, %s, name=%r>" % (
            self.id, self.experiment_hash, descr, self.name)


class Run(Base):
    """A run.

    This is created when a user submits parameters and triggers the run of an
    experiment. It contains logs and the location of output files.
    """
    __tablename__ = 'runs'

    id = Column(Integer, primary_key=True)
    experiment_hash = Column(String(64), ForeignKey('experiments.hash',
                                                    ondelete='CASCADE'))
    experiment = relationship('Experiment', uselist=False,
                              back_populates='runs')
    upload_id = Column(Integer, ForeignKey('uploads.id',
                                           ondelete='RESTRICT'))
    upload = relationship('Upload', uselist=False)
    submitted = Column(DateTime, nullable=False,
                       default=lambda: datetime.utcnow())
    started = Column(DateTime, nullable=True)
    done = Column(DateTime, nullable=True)

    submitted_ip = Column(Text, nullable=True)

    parameter_values = relationship('ParameterValue', back_populates='run')
    input_files = relationship('InputFile', back_populates='run')
    ports = relationship('RunPort', back_populates='run')

    log = relationship('RunLogLine', back_populates='run')
    output_files = relationship('OutputFile', back_populates='run')

    @property
    def short_id(self):
        return run_short_ids.encode(self.id)

    @staticmethod
    def decode_id(short_id):
        return run_short_ids.decode(short_id)

    def get_log(self, from_line=0):
        return [log.line for log in self.log[from_line:]]

    def __repr__(self):
        if self.done:
            status = "done"
        elif self.started:
            status = "started"
        else:
            status = "submitted"
        return ("<Run id=%d, experiment_hash=%r, %s, %d parameters, "
                "%d inputs, %d outputs>") % (
            self.id, self.experiment_hash, status, len(self.parameter_values),
            len(self.input_files), len(self.output_files))


class RunLogLine(Base):
    """A line of run log.

    FIXME: Storing this in the database is not a great idea.
    """
    __tablename__ = 'run_logs'

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey('runs.id', ondelete='CASCADE'))
    run = relationship('Run', uselist=False, back_populates='log')
    timestamp = Column(DateTime, nullable=False,
                       default=lambda: datetime.utcnow())
    line = Column(Text, nullable=False)

    def __repr__(self):
        return "<RunLogLine id=%d, run_id=%d>" % (self.id, self.run_id)


class ParameterValue(Base):
    """A value for a parameter in a run.
    """
    __tablename__ = 'run_parameters'

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey('runs.id', ondelete='CASCADE'))
    run = relationship('Run', uselist=False, back_populates='parameter_values')
    name = Column(Text, nullable=False)
    value = Column(Text, nullable=False)

    def __repr__(self):
        return "<ParameterValue id=%d, run_id=%d, name=%r>" % (
            self.id, self.run_id, self.name)


class RunPort(Base):
    """A network port to be exposed from the experiment container.
    """
    __tablename__ = 'run_ports'

    port_number = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey('runs.id', ondelete='CASCADE'),
                    primary_key=True)
    run = relationship('Run', uselist=False, back_populates='ports')
    type = Column(Text, nullable=False, default='http')
    map_host = Column(Text, nullable=True)


class InputFile(Base):
    """An input file for a run.
    """
    __tablename__ = 'input_files'

    id = Column(Integer, primary_key=True)
    hash = Column(String(64), nullable=False)
    run_id = Column(Integer, ForeignKey('runs.id', ondelete='CASCADE'))
    run = relationship('Run', uselist=False,
                       back_populates='input_files')
    name = Column(Text, nullable=False)
    size = Column(Integer, nullable=False)

    def __repr__(self):
        return "<InputFile id=%d, run_id=%d, hash=%r, name=%r>" % (
            self.id, self.run_id, self.hash, self.name)


class OutputFile(Base):
    """An output file from a run.
    """
    __tablename__ = 'output_files'

    id = Column(Integer, primary_key=True)
    hash = Column(String(64), nullable=False)
    run_id = Column(Integer, ForeignKey('runs.id', ondelete='CASCADE'))
    run = relationship('Run', uselist=False,
                       back_populates='output_files')
    name = Column(Text, nullable=False)
    size = Column(Integer, nullable=False)

    def __repr__(self):
        return "<OutputFile id=%d, run_id=%d, hash=%r, name=%r>" % (
            self.id, self.run_id, self.hash, self.name)


class Setting(Base):
    """Application setting and such.
    """
    __tablename__ = 'settings'

    name = Column(Text, nullable=False, primary_key=True)
    value = Column(Text, nullable=False)


def purge(url=None):
    Session = connect(url)

    session = Session()
    session.query(Experiment).delete()
    session.commit()


def connect(url=None, *, create=False):
    """Connect to the database using an environment variable.
    """
    logger.info("Connecting to SQL database")
    if url is None:
        url = 'postgresql://{user}:{password}@{host}/{database}'.format(
            user=os.environ['POSTGRES_USER'],
            password=os.environ['POSTGRES_PASSWORD'],
            host=os.environ['POSTGRES_HOST'],
            database=os.environ['POSTGRES_DB'],
        )
        engine = create_engine(url, connect_args={'connect_timeout': 10})
    else:
        engine = create_engine(url)

    start = time.perf_counter()
    while True:
        try:
            conn = engine.connect()
        except OperationalError as e:
            # Retry for 2 minutes
            if time.perf_counter() < start + 120:
                logger.info("Could not connect to database, retrying; %s", e)
                time.sleep(5)
            else:
                raise
        else:
            break

    tables_exist = engine.dialect.has_table(conn, 'experiments')

    if not tables_exist:
        if create:
            logger.warning("The tables don't seem to exist; creating")
            Base.metadata.create_all(bind=engine)
        else:
            logger.warning("The tables don't seem to exist; exiting!")
            sys.exit(1)

    DBSession = sessionmaker(bind=engine)
    db = DBSession()
    if not tables_exist:
        shortids_salt = os.getrandom(64)
        db.add(Setting(
            name='shortids_salt',
            value=b64encode(shortids_salt).decode('ascii'),
        ))
        db.commit()
    else:
        shortids_salt = db.query(Setting).get('shortids_salt')
        if shortids_salt is None:
            raise RuntimeError("Database exists but no shortids_salt set")
        shortids_salt = b64decode(shortids_salt.value.encode('ascii'))

    global run_short_ids, upload_short_ids
    run_short_ids = ShortIDs(b'run' + shortids_salt)
    upload_short_ids = ShortIDs(b'upload' + shortids_salt)

    return DBSession


def check(DBSession):
    try:
        with DBSession() as db:
            db.query(Experiment).limit(1).first()
    except Exception:
        return "Database unavailable"
    return None
