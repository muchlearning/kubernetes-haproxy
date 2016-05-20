FROM alpine:3.3
MAINTAINER Hubert Chathi <hubert@muchlearning.org>
RUN apk add --update haproxy python py-jinja2 && rm -rf /var/cache/apk/*
ENV ETCD2BASE="http://127.0.0.1:2379" \
    STATISTICS_PASSWORD="IAmAnIdiotForNotChangingTheDefaultPassword"
RUN mkdir -p /opt/haproxy/ssl
WORKDIR /opt/haproxy
COPY watch.py /opt/haproxy/
CMD ["./watch.py"]
