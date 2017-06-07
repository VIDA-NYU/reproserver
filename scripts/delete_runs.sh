#!/bin/sh

set -eu

. "$(dirname "$0")/_lib.sh"

USAGE_MSG='Usage: delete_runs.sh [k8s <tier>|docker]
Delete all runs from the database.
Note that this does not remove the input and output files from S3.'

eval "$(run_python_on_web "$USAGE_MSG" "$@")" <<'END'
from common import database
_, S = database.connect()
s = S()
s.query(database.Run).delete()
s.commit()
END
