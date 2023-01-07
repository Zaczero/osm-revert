#!/bin/sh

docker run --privileged --rm tonistiigi/binfmt --install all && \
docker buildx build --platform linux/arm64,linux/amd64 -t zaczero/osm-revert --no-cache --push .
