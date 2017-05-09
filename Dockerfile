FROM alpine:3.5
MAINTAINER Hubert Chathi <hubert@muchlearning.org>
EXPOSE 80 443 1936
ENV K8SBASE="http://127.0.0.1:8080" \
    STATISTICS_PASSWORD="IAmAnIdiotForNotChangingTheDefaultPassword"
RUN apk --no-cache add --update haproxy python py-jinja2 py-requests py-gevent ca-certificates wget \
    && wget -O /usr/local/bin/dumb-init https://github.com/Yelp/dumb-init/releases/download/v1.1.1/dumb-init_1.1.1_amd64 \
    && chmod +x /usr/local/bin/dumb-init \
    && mkdir -p /opt/haproxy/ssl
WORKDIR /opt/haproxy
COPY watch.py /opt/haproxy/
CMD ["/usr/local/bin/dumb-init", "-c", "./watch.py"]
