FROM alpine:3.3
MAINTAINER Hubert Chathi <hubert@muchlearning.org>
RUN apk add --update haproxy python py-jinja2 \
    && rm -rf /var/cache/apk/* \
    && wget -O /usr/local/bin/dumb-init https://github.com/Yelp/dumb-init/releases/download/v1.0.2/dumb-init_1.0.2_amd64 \
    && chmod +x /usr/local/bin/dumb-init
ENV ETCD2BASE="http://127.0.0.1:2379" \
    STATISTICS_PASSWORD="IAmAnIdiotForNotChangingTheDefaultPassword"
RUN mkdir -p /opt/haproxy/ssl
WORKDIR /opt/haproxy
COPY watch.py /opt/haproxy/
CMD ["/usr/local/bin/dumb-init", "-c", "./watch.py"]
