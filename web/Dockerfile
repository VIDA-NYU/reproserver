FROM python:2.7

WORKDIR /usr/src/app
COPY web/requirements.txt /usr/src/app
RUN pip install -r requirements.txt
COPY web/web /usr/src/app/web
RUN mkdir uploads
ENTRYPOINT ["python", "-m", "web"]
