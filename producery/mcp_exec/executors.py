"""CodeExecutor protocol with two implementations sharing one contract.

* SealedProcessExecutor  — compose fallback: HTTP to the sealed `executor` container.
* LangSmithSandboxExecutor — K8s / SaaS posture: LangSmith Sandboxes via the SDK.

The MCP `run_code` tool sees only the protocol; swapping implementations is an env change.
"""

from __future__ import annotations

import json
import shlex
import time
from typing import Any, Protocol

import httpx


class ExecResult(dict):
    """Result dict: stdout, stderr, exit_status, timed_out, duration_ms, artifacts and truncated."""


class CodeExecutor(Protocol):
    """Protocol every executor backend implements: a `name`, `run` and `healthy`."""

    name: str

    async def run(self, code: str, inputs: dict[str, Any], timeout_s: int) -> ExecResult:
        """Run a snippet with `inputs` bound and return its result.

        Args:
            code: Python 3 source.
            inputs: JSON object exposed to the code as `inputs`.
            timeout_s: Requested wall-clock limit in seconds.
        """
        ...

    async def healthy(self) -> bool:
        """Return True when the backend is reachable."""
        ...


class SealedProcessExecutor:
    """Compose fallback: POST each run to the sealed `executor` container over HTTP."""

    name = "sealed-process"

    def __init__(self, base_url: str, max_timeout_s: int) -> None:
        """Record the executor base URL and the timeout ceiling.

        Args:
            base_url: Base URL of the executor container; a trailing slash is stripped.
            max_timeout_s: Upper bound applied to every requested timeout.
        """
        self._url = base_url.rstrip("/")
        self._max = max_timeout_s

    async def run(self, code: str, inputs: dict[str, Any], timeout_s: int) -> ExecResult:
        """POST the snippet to `/run` and return the executor's JSON result.

        The timeout is clamped to [1, max_timeout_s] and the HTTP client waits ten seconds longer than that.

        Args:
            code: Python 3 source.
            inputs: JSON object exposed to the code as `inputs`.
            timeout_s: Requested wall-clock limit in seconds.

        Raises:
            httpx.HTTPStatusError: When the executor answers with a non-2xx status.
        """
        timeout_s = max(1, min(timeout_s, self._max))
        async with httpx.AsyncClient(timeout=timeout_s + 10) as client:
            r = await client.post(
                f"{self._url}/run", json={"code": code, "inputs": inputs, "timeout_s": timeout_s}
            )
        r.raise_for_status()
        return ExecResult(r.json())

    async def healthy(self) -> bool:
        """Return True when `GET /health` answers 200 within five seconds; False on any error."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                return (await client.get(f"{self._url}/health")).status_code == 200
        except Exception:
            return False


class LangSmithSandboxExecutor:
    """Primary path when Sandboxes are enabled on the account / cluster (KVM nodes + JuiceFS self-hosted)."""

    name = "langsmith-sandbox"

    def __init__(self, max_timeout_s: int) -> None:
        """Record the timeout ceiling.

        Args:
            max_timeout_s: Upper bound applied to every requested timeout.
        """
        self._max = max_timeout_s

    async def run(self, code: str, inputs: dict[str, Any], timeout_s: int) -> ExecResult:
        """Run the snippet in a fresh LangSmith sandbox (deleted on context exit) and return its result.

        The code is written to `/tmp/main.py` in the sandbox and executed with `python3 -I` under the
        `timeout` command, so exit status 124 is reported as `timed_out`. Stdout keeps its first 16,000
        characters and stderr its last 4,000; `artifacts` is always empty and `truncated` always False. The
        SDK is imported lazily so the module loads without it.

        Args:
            code: Python 3 source.
            inputs: JSON object exposed to the code as `inputs`.
            timeout_s: Requested wall-clock limit, clamped to [1, max_timeout_s].
        """
        from langsmith.sandbox import SandboxClient

        timeout_s = max(1, min(timeout_s, self._max))
        client = SandboxClient()
        t0 = time.monotonic()
        with client.sandbox() as sb:  # fresh microVM per run; context exit deletes it
            sb.run(f"printf %s {shlex.quote(code)} > /tmp/main.py")
            cmd = (
                f"cd /tmp && PRODUCERY_INPUTS={shlex.quote(json.dumps(inputs or {}))} "
                f"timeout {timeout_s}s python3 -I -c "
                + shlex.quote(
                    "import json,os;inputs=json.loads(os.environ.get('PRODUCERY_INPUTS') or '{}');"
                    "exec(compile(open('main.py').read(),'main.py','exec'),{'__name__':'__main__','inputs':inputs})"
                )
            )
            res = sb.run(cmd, timeout=timeout_s + 10)  # the shell `timeout` is the limit that reports 124
        exit_status = int(getattr(res, "exit_code", 0) or 0)
        return ExecResult(
            stdout=str(getattr(res, "stdout", ""))[:16_000],
            stderr=str(getattr(res, "stderr", ""))[-4_000:],
            exit_status=exit_status,
            timed_out=exit_status == 124,
            duration_ms=int((time.monotonic() - t0) * 1000),
            artifacts=[],
            truncated=False,
        )

    async def healthy(self) -> bool:
        """Return True when the sandbox SDK imports and a `SandboxClient` constructs; False otherwise."""
        try:
            from langsmith.sandbox import SandboxClient

            SandboxClient()
            return True
        except Exception:
            return False
