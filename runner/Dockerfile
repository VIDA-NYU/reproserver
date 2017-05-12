FROM python:2.7

WORKDIR /usr/src/app
RUN curl -Lo /tmp/docker.tgz https://get.docker.com/builds/Linux/x86_64/docker-17.05.0-ce.tgz && \
    tar -xf /tmp/docker.tgz -C /usr/local && \
    rm /tmp/docker.tgz
COPY runner/requirements.txt /usr/src/app
RUN pip install -r requirements.txt
COPY runner/runner /usr/src/app/runner
COPY common/ /usr/src/app/
CMD ["python", "-m", "runner"]
