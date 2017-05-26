FROM python:2.7

WORKDIR /usr/src/app
COPY init/requirements.txt /usr/src/app
RUN pip install -r requirements.txt
COPY init/init.py /usr/src/app/init.py
COPY common/ /usr/src/app/
RUN useradd -d /usr/src/app -s /usr/sbin/nologin appuser
USER appuser
ENTRYPOINT ["python", "-c", "from init import main; main()"]
