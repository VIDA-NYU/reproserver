FROM python:2.7

WORKDIR /usr/src/app
COPY init/requirements.txt /usr/src/app
RUN pip install -r requirements.txt
COPY init/init.py /usr/src/app/init.py
COPY common/ /usr/src/app/
RUN mkdir /usr/src/app/home && \
    useradd -d /usr/src/app/home -s /usr/sbin/nologin appuser && \
    chown appuser /usr/src/app/home
USER appuser
ENV HOME=/usr/src/app/home
ENTRYPOINT ["python", "-c", "from init import main; main()"]
