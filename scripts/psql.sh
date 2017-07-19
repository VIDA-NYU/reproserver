#!/usr/bin/env bash

set -eu

if [ "$#" = 0 ]; then
    echo 'Usage: psql.sh k8s <tier>
Get a postgres shell.' >&2
    exit 1
fi

case "$1" in
    k8s)
        if [ "$#" != 2 ]; then exit 1; fi
        TIER="$2"
        kubectl run --rm -ti --restart=Never psql --image=postgres:9.6 \
            psql --overrides '{
              "apiVersion": "v1",
              "kind": "Pod",
              "spec": {
                "containers": [
                  { "name": "psql",
                    "image": "postgres:9.6",
                    "stdin": true, "stdinOnce": true, "tty": true,
                    "args": ["psql", "-h", "reproserver-postgres-'"$TIER"'", "-U", "$(POSTGRES_USER)"],
                     "env": [
                       { "name": "POSTGRES_USER",
                         "valueFrom": { "secretKeyRef": {
                         "name": "reproserver-secret-'"$TIER"'",
                         "key": "user"}}},
                       { "name": "PGPASSWORD",
                         "valueFrom": {"secretKeyRef": {
                         "name": "reproserver-secret-'"$TIER"'",
                         "key": "password"}}}
                     ]
                  }
                ]
              }
            }'
    ;;
esac
