FROM python:2.7

WORKDIR /usr/src/app
RUN curl -Lo /tmp/docker.tgz https://get.docker.com/builds/Linux/x86_64/docker-17.05.0-ce.tgz && \
    tar -xf /tmp/docker.tgz -C /usr/local && \
    rm /tmp/docker.tgz
COPY builder/reprozip /usr/src/app/reprozip
RUN pip install \
    -e /usr/src/app/reprozip/reprounzip \
    -e /usr/src/app/reprozip/reprounzip-docker
COPY builder/requirements.txt /usr/src/app
RUN pip install -r requirements.txt
COPY builder/builder /usr/src/app/builder
COPY common/ /usr/src/app/
RUN useradd -d /usr/src/app -s /usr/sbin/nologin appuser
USER appuser
CMD ["/bin/sh", "-c", "PATH=\"/usr/local/docker:$PATH\" python -m builder"]
