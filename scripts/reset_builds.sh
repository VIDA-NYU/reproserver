#!/bin/sh

set -eu

. "$(dirname "$0")/_lib.sh"

USAGE_MSG='Usage: reset_builds.sh <k8s|docker>
Invalidates the experiments, they will have to be built again.
Note that this does not actually delete the images; you should probably restart the registry.'

eval "$(run_python_on_web "$USAGE_MSG" "$@")" <<'END'
from reproserver import database
S = database.connect()
s = S()
s.query(database.Experiment).update({
    database.Experiment.status: database.Status.NOBUILD
})
s.commit()
END
