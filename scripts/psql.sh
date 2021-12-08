#!/usr/bin/env bash

set -eu

kubectl run --rm -ti --restart=Never psql --image=postgres:12.6 \
    psql --overrides '{
      "apiVersion": "v1",
      "kind": "Pod",
      "spec": {
        "containers": [
          { "name": "psql",
            "image": "postgres:12.6",
            "stdin": true, "stdinOnce": true, "tty": true,
            "args": ["psql", "-h", "postgres", "-U", "$(POSTGRES_USER)", "reproserver"],
             "env": [
               { "name": "POSTGRES_USER",
                 "valueFrom": { "secretKeyRef": {
                 "name": "reproserver-secret",
                 "key": "postgres_user"}}},
               { "name": "PGPASSWORD",
                 "valueFrom": {"secretKeyRef": {
                 "name": "reproserver-secret",
                 "key": "postgres_password"}}}
             ]
          }
        ]
      }
    }'
