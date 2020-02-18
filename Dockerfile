FROM alpine:3

# TODO: determine and pin versions
RUN apk update \
 && apk add --no-cache py3-zmq \
 && pip3 install -U --no-cache \
    aiozmq==0.7.1 \
 && adduser -D runner

USER runner
