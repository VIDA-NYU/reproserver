FROM python:3.6

RUN curl -sSL https://raw.githubusercontent.com/sdispater/poetry/master/get-poetry.py | python && \
    /root/.poetry/bin/poetry config settings.virtualenvs.create false

RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

# Install dependencies
COPY pyproject.toml poetry.lock /usr/src/app/
RUN /root/.poetry/bin/poetry install --no-interaction --no-dev

# Install package
COPY reproserver /usr/src/app/reproserver
COPY README.rst LICENSE.txt /usr/src/app/
RUN /root/.poetry/bin/poetry install --no-interaction --no-dev

EXPOSE 8000
CMD ["reproserver"]
