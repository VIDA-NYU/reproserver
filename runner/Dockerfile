FROM python:2.7

WORKDIR /usr/src/app
COPY runner/requirements.txt /usr/src/app
RUN pip install -r requirements.txt
COPY runner/runner /usr/src/app/runner
CMD ["python", "-m", "runner"]
