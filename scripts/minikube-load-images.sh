#!/bin/bash

set -eu
set -o pipefail

if [ "x${DOCKER_HOST-}" = x ]; then
    echo "DOCKER_HOST is not set; running 'eval \$(minikube docker-env)" >&2
    eval $(minikube docker-env)
else
    echo "DOCKER_HOST is set" >&2
fi

for image in reproserver_web; do
    echo "Loading image $image..."
    DOCKER_HOST= sudo -g docker docker save $image | docker load
done
