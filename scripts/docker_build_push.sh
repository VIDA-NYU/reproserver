#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

VERSION=$(git describe)

IMAGE=ghcr.io/vida-nyu/reproserver/web:$VERSION

docker build --pull -t $IMAGE .
docker push $IMAGE

echo
echo "    $IMAGE"
