FROM python:3.8

RUN curl -sSL https://raw.githubusercontent.com/sdispater/poetry/master/get-poetry.py | python - --version 1.1.12 && \
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
RUN curl -Lo /tmp/docker.tgz https://download.docker.com/linux/static/stable/x86_64/docker-20.10.7.tgz && \
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
