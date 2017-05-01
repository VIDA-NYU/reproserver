FROM python:2.7

RUN pip install flask
COPY run.py /usr/src/app/
WORKDIR /usr/src/app
ENTRYPOINT python run.py
