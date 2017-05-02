FROM python:2.7

WORKDIR /usr/src/app
COPY requirements.txt /usr/src/app
RUN pip install -r requirements.txt
COPY builder /usr/src/app/builder
ENTRYPOINT ["python", "-m", "builder"]
