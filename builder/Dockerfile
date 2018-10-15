FROM python:3.6

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
RUN mkdir /usr/src/app/home && \
    useradd -d /usr/src/app/home -s /usr/sbin/nologin appuser && \
    chown appuser /usr/src/app/home
USER appuser
ENV HOME=/usr/src/app/home
CMD ["/bin/sh", "-c", "PATH=\"/usr/local/docker:$PATH\" python -m builder"]
