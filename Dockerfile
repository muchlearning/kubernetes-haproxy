FROM alpine:3.3
MAINTAINER Hubert Chathi <hubert@muchlearning.org>
EXPOSE 80 443 1936
RUN apk add --update haproxy python py-jinja2 \
    && rm -rf /var/cache/apk/*
COPY dumb-init /usr/local/bin/
ENV ETCD2BASE="http://127.0.0.1:2379" \
    STATISTICS_PASSWORD="IAmAnIdiotForNotChangingTheDefaultPassword"
RUN mkdir -p /opt/haproxy/ssl
WORKDIR /opt/haproxy
COPY watch.py /opt/haproxy/
CMD ["/usr/local/bin/dumb-init", "-c", "./watch.py"]
