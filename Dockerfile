FROM python:3.7

RUN curl -sSL https://raw.githubusercontent.com/sdispater/poetry/master/get-poetry.py | python && \
    /root/.poetry/bin/poetry config virtualenvs.create false

ENV TINI_VERSION v0.18.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /tini
RUN chmod +x /tini

RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

# Copy reprounzip-docker
COPY reprozip /usr/src/app/reprozip

# Install dependencies
COPY pyproject.toml poetry.lock /usr/src/app/
RUN /root/.poetry/bin/poetry install --no-interaction --no-dev

# Install Docker
RUN curl -Lo /tmp/docker.tgz https://get.docker.com/builds/Linux/x86_64/docker-17.05.0-ce.tgz && \
    tar -xf /tmp/docker.tgz -C /usr/local && \
    mv /usr/local/docker/* /usr/local/bin/ && \
    rmdir /usr/local/docker && \
    rm /tmp/docker.tgz

# Install package
COPY reproserver /usr/src/app/reproserver
COPY README.rst LICENSE.txt /usr/src/app/
RUN /root/.poetry/bin/poetry install --no-interaction --no-dev

# Set up user
RUN mkdir /usr/src/app/home && \
    useradd -d /usr/src/app/home -s /usr/sbin/nologin appuser && \
    chown appuser /usr/src/app/home
USER appuser
ENV HOME=/usr/src/app/home

EXPOSE 8000
ENTRYPOINT ["/tini", "--"]
CMD ["reproserver"]
