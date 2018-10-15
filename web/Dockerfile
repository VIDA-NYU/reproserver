FROM python:3.6

RUN pip install uwsgi~=2.0.15
WORKDIR /usr/src/app
COPY web/requirements.txt /usr/src/app
RUN pip install -r requirements.txt
COPY web/web /usr/src/app/web
COPY common/ /usr/src/app/
COPY web/entrypoint.sh /usr/src/app/
RUN chmod +x entrypoint.sh
RUN mkdir /usr/src/app/home && \
    useradd -d /usr/src/app/home -s /usr/sbin/nologin appuser && \
    chown appuser /usr/src/app/home
ENV HOME=/usr/src/app/home
EXPOSE 8000
ENTRYPOINT ["/usr/src/app/entrypoint.sh"]
CMD ["server"]
