FROM python:2.7

WORKDIR /usr/src/app
COPY builder/requirements.txt /usr/src/app
RUN pip install -r requirements.txt
COPY builder/builder /usr/src/app/builder
CMD ["python", "-m", "builder"]
