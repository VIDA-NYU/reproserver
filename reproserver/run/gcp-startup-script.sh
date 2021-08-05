#!/bin/bash
set -eu

# Set up logging
exec 2>/var/log/reproserver-install.log 1>&2

export HOME=/root
cd /root

# Install packages
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -yy python3 python3-pip git docker.io libpq-dev

# Retrieve metadata
RUN="$(curl http://metadata.google.internal/computeMetadata/v1/instance/attributes/reproserver-run -H "Metadata-Flavor: Google")"
REPOSITORY="$(curl http://metadata.google.internal/computeMetadata/v1/instance/attributes/reproserver-repo -H "Metadata-Flavor: Google")"
REVISION="$(curl http://metadata.google.internal/computeMetadata/v1/instance/attributes/reproserver-revision -H "Metadata-Flavor: Google")"
API_ENDPOINT="$(curl http://metadata.google.internal/computeMetadata/v1/instance/attributes/reproserver-api -H "Metadata-Flavor: Google")"

# Get reproserver from Git repository
git clone "$REPOSITORY"
cd reproserver
git checkout "$REVISION"
git submodule init
git submodule update

# Install Poetry
curl -sSL https://raw.githubusercontent.com/sdispater/poetry/master/get-poetry.py | python3 - --version 1.1.4

# Install dependencies
$HOME/.poetry/bin/poetry install --no-interaction --no-dev

# Call back into runner
exec $HOME/.poetry/bin/poetry run python -c "import sys; from reproserver.run.gcp import GcpRunner; GcpRunner._run_in_vm(*sys.argv[1:])" "$API_ENDPOINT" "$RUN"
