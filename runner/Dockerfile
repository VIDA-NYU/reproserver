FROM python:3.6

RUN curl -sSL https://raw.githubusercontent.com/sdispater/poetry/master/get-poetry.py | python && \
    /root/.poetry/bin/poetry config settings.virtualenvs.create false

RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

# Install dependencies
COPY pyproject.toml poetry.lock /usr/src/app/
RUN /root/.poetry/bin/poetry install --no-interaction --no-dev

# Install Docker
RUN curl -Lo /tmp/docker.tgz https://get.docker.com/builds/Linux/x86_64/docker-17.05.0-ce.tgz && \
    tar -xf /tmp/docker.tgz -C /usr/local && \
    rm /tmp/docker.tgz

# Install package
COPY reproserver /usr/src/app/reproserver
COPY README.rst LICENSE.txt /usr/src/app/
RUN /root/.poetry/bin/poetry install --no-interaction --no-dev

# Install runner
COPY runner/runner /usr/src/app/runner

# Set up user
RUN mkdir /usr/src/app/home && \
    useradd -d /usr/src/app/home -s /usr/sbin/nologin appuser && \
    chown appuser /usr/src/app/home
USER appuser
ENV HOME=/usr/src/app/home

CMD ["/bin/sh", "-c", "PATH=\"/usr/local/docker:$PATH\" python -m runner"]
