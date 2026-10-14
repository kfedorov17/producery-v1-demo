"""Executor contract (runs on Linux only — the sealed executor uses POSIX rlimits; CI and the containers are Linux)."""

import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="POSIX rlimits; exercised live in the container"
)


def test_run_snippet_contract(tmp_path):
    os.environ["EXEC_WORK_ROOT"] = str(tmp_path)
    from producery.executor.server import run_snippet

    ok = run_snippet("import json\nprint(json.dumps({'s': sum(inputs['xs'])}))", {"xs": [1, 2]}, 5)
    assert ok["exit_status"] == 0 and '"s": 3' in ok["stdout"] and ok["timed_out"] is False
    loop = run_snippet("while True: pass", {}, 1)
    assert loop["timed_out"] is True and loop["exit_status"] != 0
    assert not list(tmp_path.iterdir()), "work dir destroyed after the run"
