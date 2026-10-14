"""Run model-generated Python in a sealed process, the compose stand-in for LangSmith Sandboxes.

The container has no secrets, no env, no egress (compose `internal` network), a read-only root filesystem, a tmpfs
work directory and cpu, memory and pid limits; it drops every capability except the few the daemon needs to switch
users and clean up. Each request runs as a fresh `python -I` subprocess under an unprivileged user in a fresh temp
directory with rlimits and a wall clock; the directory is destroyed afterwards. Standard library only (`http.server`)
so the image carries nothing else.

Contract (identical to the LangSmith Sandbox executor):
  POST /run {code, inputs, timeout_s} → {stdout, stderr, exit_status, timed_out, duration_ms, artifacts}
A timeout is a structured result, not an error.
"""

from __future__ import annotations

import base64
import json
import os
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_STDOUT = 16_000
MAX_STDERR = 4_000
MAX_ARTIFACT_BYTES = 256_000
MAX_ARTIFACTS = 4
MAX_TIMEOUT = int(os.environ.get("EXEC_MAX_TIMEOUT_S", "60"))
MEM_BYTES = int(os.environ.get("EXEC_MEM_BYTES", str(256 * 1024 * 1024)))
WORK_ROOT = os.environ.get("EXEC_WORK_ROOT", "/work")
SANDBOX_USER = os.environ.get("EXEC_SANDBOX_USER", "sandbox")


def _sandbox_ids() -> tuple[int, int] | None:
    """uid/gid of the unprivileged runner, or None when the daemon itself is unprivileged (tests)."""
    if os.geteuid() != 0:
        return None
    pw = pwd.getpwnam(SANDBOX_USER)
    return pw.pw_uid, pw.pw_gid


# The runner applies the rlimits itself, as the unprivileged user, before touching the snippet: Popen's `user`/`group`/
# `start_new_session` do the privilege drop and the process group, so no `preexec_fn` runs inside a multithreaded daemon
# (the Python docs warn it can deadlock the child).
RUNNER = """import json, os, sys, io, resource
_mem = int(os.environ["PRODUCERY_MEM_BYTES"])
resource.setrlimit(resource.RLIMIT_AS, (_mem, _mem))
resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))
resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
inputs = json.loads(os.environ.get("PRODUCERY_INPUTS") or "{}")
src = open("main.py", encoding="utf-8").read()
ns = {"__name__": "__main__", "inputs": inputs}
exec(compile(src, "main.py", "exec"), ns)
"""


def run_snippet(code: str, inputs: dict, timeout_s: int) -> dict:
    """Run one snippet in a fresh `python -I` subprocess in a temporary run directory and return the result.

    The timeout is clamped to [1, MAX_TIMEOUT]. When the daemon runs as root the run directory and its files
    are chowned to the sandbox user and the child is started with that uid/gid (Popen `user`/`group`); the
    rlimits (address space, process count, file size, core) are applied by the runner script in the child.
    On timeout the whole process group is SIGKILLed and the result carries `timed_out=True` with exit status
    -9. Up to MAX_ARTIFACTS files from `out/` of at most MAX_ARTIFACT_BYTES each are returned base64-encoded.
    The run directory is removed after the try/except, not in a `finally`, so an exception raised before that
    line (from Popen or while collecting artifacts) leaves it in place.

    Args:
        code: Python source written to `main.py` in the run directory.
        inputs: JSON-serialisable object exposed to the code as `inputs`.
        timeout_s: Requested wall-clock limit in seconds.

    Returns:
        `{stdout, stderr, exit_status, timed_out, duration_ms, artifacts, truncated}`; stdout keeps its first
        MAX_STDOUT characters and stderr its last MAX_STDERR.
    """
    timeout_s = max(1, min(int(timeout_s or 20), MAX_TIMEOUT))
    workdir = Path(tempfile.mkdtemp(prefix="run-", dir=WORK_ROOT))
    (workdir / "out").mkdir()
    (workdir / "main.py").write_text(code, encoding="utf-8")
    (workdir / "runner.py").write_text(RUNNER, encoding="utf-8")
    ids = _sandbox_ids()
    if ids is not None:  # the run dir is the ONLY writable location for the snippet
        for path in (workdir, workdir / "out", workdir / "main.py", workdir / "runner.py"):
            os.chown(path, ids[0], ids[1])
        os.chmod(workdir, 0o700)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PRODUCERY_INPUTS": json.dumps(inputs or {}),
        "PRODUCERY_MEM_BYTES": str(MEM_BYTES),
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
    }
    drop: dict = {"user": ids[0], "group": ids[1], "extra_groups": []} if ids is not None else {}
    t0 = time.monotonic()
    timed_out = False
    proc = subprocess.Popen(
        [sys.executable, "-I", "runner.py"],
        cwd=workdir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # own process group → killpg on timeout
        **drop,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
        exit_status = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        exit_status = -9
    duration_ms = int((time.monotonic() - t0) * 1000)
    artifacts = []
    for p in sorted((workdir / "out").iterdir())[:MAX_ARTIFACTS]:
        st = p.lstat()  # never follow a link: a snippet could point one at any file in the container
        if stat.S_ISREG(st.st_mode) and st.st_size <= MAX_ARTIFACT_BYTES:
            artifacts.append({"name": p.name, "bytes_b64": base64.b64encode(p.read_bytes()).decode()})
    shutil.rmtree(workdir, ignore_errors=True)  # fresh environment per run — destroyed, never reused
    stdout = out.decode("utf-8", "replace")
    stderr = err.decode("utf-8", "replace")
    return {
        "stdout": stdout[:MAX_STDOUT],
        "stderr": stderr[-MAX_STDERR:],
        "exit_status": exit_status,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "artifacts": artifacts,
        "truncated": len(stdout) > MAX_STDOUT or len(stderr) > MAX_STDERR,
    }


class Handler(BaseHTTPRequestHandler):
    """HTTP handler for the daemon: `GET /health` and `POST /run`; access logs go to stdout as JSON lines."""

    def log_message(self, fmt: str, *args) -> None:
        """Write the access-log line to stdout as a JSON object instead of the default stderr text.

        Args:
            fmt: printf-style format string supplied by the base class.
            *args: Values interpolated into `fmt`.
        """
        sys.stdout.write(json.dumps({"logger": "executor", "msg": fmt % args}) + "\n")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Serve `/health` with the daemon status and timeout ceiling; any other path returns 404."""
        if self.path == "/health":
            self._json(200, {"ok": True, "service": "executor", "max_timeout_s": MAX_TIMEOUT})
        else:
            self._json(404, {"error": {"code": "not_found", "message": "unknown path"}})

    def do_POST(self) -> None:
        """Serve `/run`: parse `{code, inputs, timeout_s}`, run the snippet and return its result as JSON.

        Empty code returns 400 `empty_code`; any exception while reading or running returns 500
        `executor_failure`; other paths return 404.
        """
        if self.path != "/run":
            self._json(404, {"error": {"code": "not_found", "message": "unknown path"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
            code = str(req.get("code") or "")
            if not code.strip():
                self._json(400, {"error": {"code": "empty_code", "message": "code is required"}})
                return
            result = run_snippet(code, req.get("inputs") or {}, int(req.get("timeout_s") or 20))
            self._json(200, result)
        except Exception as e:
            self._json(500, {"error": {"code": "executor_failure", "message": str(e)[:300]}})


def main() -> None:
    """Create the work root with mode 0o755 and serve the daemon on 0.0.0.0:9000 with a threading server."""
    Path(WORK_ROOT).mkdir(parents=True, exist_ok=True)
    os.chmod(WORK_ROOT, 0o755)  # noqa: S103 — root-owned: the runner can create nothing here, only inside its own run dir
    server = ThreadingHTTPServer(("0.0.0.0", 9000), Handler)  # noqa: S104 — internal network only
    sys.stdout.write(json.dumps({"logger": "executor", "msg": "executor_started", "port": 9000}) + "\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
