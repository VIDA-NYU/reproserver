FROM python:2.7

WORKDIR /usr/src/app
COPY requirements.txt /usr/src/app
RUN pip install -r requirements.txt
COPY web /usr/src/app/web
ENTRYPOINT ["python", "-m", "web"]
