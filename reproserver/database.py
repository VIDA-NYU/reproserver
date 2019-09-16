import enum
import logging
import os
from sqlalchemy import Column, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.sql import functions
from sqlalchemy.types import Boolean, DateTime, Enum, Integer, String

from .shortid import MultiShortIDs


Base = declarative_base()


class Status(enum.Enum):
    NOBUILD = 1
    QUEUED = 2
    BUILDING = 3
    BUILT = 4
    ERROR = 0


class Experiment(Base):
    """Experiments available on the server.

    Those match experiment files that were uploaded, whether or not an image
    has been built.

    Note that no filename is here, since the file might have been uploaded
    multiple times with different names.
    """
    __tablename__ = 'experiments'

    hash = Column(String, primary_key=True)
    status = Column(Enum(Status), nullable=False, default=Status.NOBUILD)
    docker_image = Column(String, nullable=True)
    last_access = Column(DateTime, nullable=False,
                         server_default=functions.now())

    uploads = relationship('Upload', back_populates='experiment')
    runs = relationship('Run', back_populates='experiment')
    parameters = relationship('Parameter', back_populates='experiment')
    paths = relationship('Path', back_populates='experiment')
    log = relationship('BuildLogLine', back_populates='experiment')

    def get_log(self, from_line=0):
        return [log.line for log in self.log[from_line:]]

    def __repr__(self):
        return "<Experiment hash=%r, status=%r, docker_image=%r>" % (
            self.hash,
            self.status,
            self.docker_image)


class Upload(Base):
    """An upload of an experiment.

    There can be multiple uploads for the same experiment, each of them
    associated with a different uploader and filename.

    This is not used by the application, but might be important for accounting
    purposes.
    """
    __tablename__ = 'uploads'

    id = Column(Integer, primary_key=True)
    filename = Column(String, nullable=False)
    experiment_hash = Column(String, ForeignKey('experiments.hash',
                                                ondelete='CASCADE'))
    experiment = relationship('Experiment', uselist=False,
                              back_populates='uploads')
    submitted_ip = Column(String, nullable=True)
    repository_key = Column(String, nullable=True, index=True)
    timestamp = Column(DateTime, nullable=False,
                       server_default=functions.now())

    @property
    def short_id(self):
        return short_ids.encode('upload', self.id)

    def __repr__(self):
        return ("<Upload id=%d, experiment_hash=%r, filename=%r, "
                "submitted_ip=%r, timestamp=%r>") % (
            self.id, self.experiment_hash, self.filename,
            self.submitted_ip, self.timestamp)


class Parameter(Base):
    """An experiment parameter.

    Once the experiment has been built, the builder adds the list of its
    parameters to the database, that it extracted from the package metadata.
    Those are displayed to the user when running the experiment.
    """
    __tablename__ = 'parameters'

    id = Column(Integer, primary_key=True)
    experiment_hash = Column(String, ForeignKey('experiments.hash',
                                                ondelete='CASCADE'))
    experiment = relationship('Experiment', uselist=False,
                              back_populates='parameters')
    name = Column(String, nullable=False)
    description = Column(String, nullable=False)
    optional = Column(Boolean, nullable=False)
    default = Column(String, nullable=True)

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
    experiment_hash = Column(String, ForeignKey('experiments.hash',
                                                ondelete='CASCADE'))
    experiment = relationship('Experiment', uselist=False,
                              back_populates='paths')
    is_input = Column(Boolean, nullable=False)
    is_output = Column(Boolean, nullable=False)
    name = Column(String, nullable=False)
    path = Column(String, nullable=False)

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
    experiment_hash = Column(String, ForeignKey('experiments.hash',
                                                ondelete='CASCADE'))
    experiment = relationship('Experiment', uselist=False,
                              back_populates='runs')
    upload_id = Column(Integer, ForeignKey('uploads.id',
                                           ondelete='RESTRICT'))
    upload = relationship('Upload', uselist=False)
    submitted = Column(DateTime, nullable=False,
                       server_default=functions.now())
    started = Column(DateTime, nullable=True)
    done = Column(DateTime, nullable=True)

    parameter_values = relationship('ParameterValue', back_populates='run')
    input_files = relationship('InputFile', back_populates='run')
    ports = relationship('RunPort', back_populates='run')

    log = relationship('RunLogLine', back_populates='run')
    output_files = relationship('OutputFile', back_populates='run')

    @property
    def short_id(self):
        return short_ids.encode('run', self.id)

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


class BuildLogLine(Base):
    """A line of build log.

    FIXME: Storing this in the database is not a great idea.
    """
    __tablename__ = 'build_logs'

    id = Column(Integer, primary_key=True)
    experiment_hash = Column(String, ForeignKey('experiments.hash',
                                                ondelete='CASCADE'))
    experiment = relationship('Experiment', uselist=False,
                              back_populates='log')
    timestamp = Column(DateTime, nullable=False,
                       server_default=functions.now())
    line = Column(String, nullable=False)

    def __repr__(self):
        return "<BuildLogLine id=%d, experiment_hash=%r>" % (
            self.id, self.experiment_hash)


class RunLogLine(Base):
    """A line of run log.

    FIXME: Storing this in the database is not a great idea.
    """
    __tablename__ = 'run_logs'

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey('runs.id', ondelete='CASCADE'))
    run = relationship('Run', uselist=False, back_populates='log')
    timestamp = Column(DateTime, nullable=False,
                       server_default=functions.now())
    line = Column(String, nullable=False)

    def __repr__(self):
        return "<RunLogLine id=%d, run_id=%d>" % (self.id, self.run_id)


class ParameterValue(Base):
    """A value for a parameter in a run.
    """
    __tablename__ = 'run_parameters'

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey('runs.id', ondelete='CASCADE'))
    run = relationship('Run', uselist=False, back_populates='parameter_values')
    name = Column(String, nullable=False)
    value = Column(String, nullable=False)

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
    type = Column(String, nullable=False, default='http')
    map_host = Column(String, nullable=True)


class InputFile(Base):
    """An input file for a run.
    """
    __tablename__ = 'input_files'

    id = Column(Integer, primary_key=True)
    hash = Column(String, nullable=False)
    run_id = Column(Integer, ForeignKey('runs.id', ondelete='CASCADE'))
    run = relationship('Run', uselist=False,
                       back_populates='input_files')
    name = Column(String, nullable=False)
    size = Column(Integer, nullable=False)

    def __repr__(self):
        return "<InputFile id=%d, run_id=%d, hash=%r, name=%r>" % (
            self.id, self.run_id, self.hash, self.name)


class OutputFile(Base):
    """An output file from a run.
    """
    __tablename__ = 'output_files'

    id = Column(Integer, primary_key=True)
    hash = Column(String, nullable=False)
    run_id = Column(Integer, ForeignKey('runs.id', ondelete='CASCADE'))
    run = relationship('Run', uselist=False,
                       back_populates='output_files')
    name = Column(String, nullable=False)
    size = Column(Integer, nullable=False)

    def __repr__(self):
        return "<OutputFile id=%d, run_id=%d, hash=%r, name=%r>" % (
            self.id, self.run_id, self.hash, self.name)


def purge(url=None):
    _, Session = connect(url)

    session = Session()
    session.query(Experiment).delete()
    session.commit()


def connect(url=None):
    """Connect to the database using an environment variable.
    """
    global short_ids
    short_ids = MultiShortIDs(os.environ['SHORTIDS_SALT'])

    logging.info("Connecting to SQL database")
    if url is None:
        url = 'postgresql://{user}:{password}@{host}/{database}'.format(
            user=os.environ['POSTGRES_USER'],
            password=os.environ['POSTGRES_PASSWORD'],
            host=os.environ['POSTGRES_HOST'],
            database=os.environ['POSTGRES_DB'],
        )
    engine = create_engine(url, echo=False)

    if not engine.dialect.has_table(engine.connect(), 'experiments'):
        logging.warning("The tables don't seem to exist; creating")
        Base.metadata.create_all(bind=engine)

    return engine, sessionmaker(bind=engine)
