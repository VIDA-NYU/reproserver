#!/bin/sh

set -eu

. "$(dirname "$0")/_lib.sh"

USAGE_MSG='Usage: delete_experiments.sh <k8s|docker>
Delete all experiments from the database.
Note that this does not remove the input and output files from S3.'

eval "$(run_python_on_web "$USAGE_MSG" "$@")" <<'END'
from reproserver import database
S = database.connect()
s = S()
s.query(database.Experiment).delete()
s.commit()
END
