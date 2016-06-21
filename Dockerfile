FROM alpine:3.3
MAINTAINER Hubert Chathi <hubert@muchlearning.org>
EXPOSE 80
RUN apk add --update varnish python py-jinja2 ca-certificates \
    && rm -rf /var/cache/apk/* \
    && wget -O /usr/local/bin/dumb-init https://github.com/Yelp/dumb-init/releases/download/v1.1.1/dumb-init_1.1.1_amd64 \
    && chmod +x /usr/local/bin/dumb-init
WORKDIR /opt/varnish
COPY watch.py /opt/varnish/
CMD ["/usr/local/bin/dumb-init", "-c", "./watch.py"]
