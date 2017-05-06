FROM python:2.7

WORKDIR /usr/src/app
COPY web/requirements.txt /usr/src/app
RUN pip install -r requirements.txt
COPY web/web /usr/src/app/web
COPY common/ /usr/src/app/
RUN mkdir uploads
EXPOSE 8000
CMD ["python", "-m", "web"]
