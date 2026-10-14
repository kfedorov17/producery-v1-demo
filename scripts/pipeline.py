"""Pipeline runner: executes a stage from pipeline/pipeline.yaml. Gates block; there is no skip flag.

python scripts/pipeline.py ci | cd | post
python scripts/pipeline.py wait-ready | pull-images | deploy --tag X | promote --tag X | rollback
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
PIPE = ROOT / "pipeline" / "pipeline.yaml"
DEPLOY_DIR = ROOT / "pipeline" / ".deploy"
AGENT = os.environ.get(
    "EVAL_AGENT_URL", "http://localhost:8010"
)  # the Settings name; this script imports nothing from the package
SERVICES = {
    "agent": f"{AGENT}/ready",
    "mcp-data": "http://localhost:8091/health",
    "mcp-exec": "http://localhost:8092/health",
    "worker": "http://localhost:8090/health",
    "web": "http://localhost:3010/api/health",
}


def git_sha() -> str:
    """Return the short HEAD sha, or `"nogit"` when git is unavailable or the tree is not a repository."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT, check=True
        ).stdout.strip()
    except Exception:
        return "nogit"


def _py(cmd: str) -> str:
    """Run every `python …` segment of a step with the interpreter running the pipeline (the venv)."""
    return re.sub(r"(^|&&\s*)python ", lambda m: f'{m.group(1)}"{sys.executable}" ', cmd)


def _env_value(name: str) -> str:
    """Read one variable from the repository `.env`, falling back to the process environment."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].split("#")[0].strip()
    return os.environ.get(name, "")


def model_door_available() -> bool:
    """Return whether the configured model door answers a one-token completion from this host.

    `/ready` only says a URL and a key are set; the LLM Gateway answers `/models` with 200 on an organization
    where completions are refused with 403, so the probe is a real chat completion on the `fast` tier, with one
    output token. `host.docker.internal` is the containers' name for this host and is probed as the loopback.
    """
    base = _env_value("LANGSMITH_GATEWAY_BASE_URL").replace("host.docker.internal", "127.0.0.1").rstrip("/")
    key = _env_value("LANGSMITH_API_KEY")
    if not base:
        return False
    try:
        tiers = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))["tiers"]
        body = {
            "model": tiers["fast"]["model"],
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
        r = httpx.post(
            f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=90
        )
        print(f"model door probe: {base} -> HTTP {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print(f"model door probe: {base} -> {type(e).__name__}")
        return False


def run_step(step: dict, sha: str) -> int:
    """Run one pipeline step through the shell and return its exit code.

    `${GIT_SHA}` is substituted in the command and in the step's `env` values, and `GIT_SHA` is exported. A step
    marked `needs_model_door` runs its `without_door` command instead when the agent reports no model door, and
    says so: the model-dependent datasets are then gated on a host that has one.

    Args:
        step: Step mapping from pipeline.yaml (`name`, `run`, optional `env`, `needs_model_door`, `without_door`).
        sha: The git sha substituted into the step.
    """
    run = step["run"]
    if step.get("needs_model_door") and not model_door_available():
        print(
            f"!!! {step['name']}: no model door on this host (the completion probe was refused); "
            "running the model-free datasets only. The model-dependent datasets are gated where a door exists."
        )
        run = step["without_door"]
    cmd = _py(run.replace("${GIT_SHA}", sha))
    env = {**os.environ, "GIT_SHA": sha}
    for k, v in (step.get("env") or {}).items():
        env[k] = str(v).replace("${GIT_SHA}", sha)
    t0 = time.time()
    print(f"\n=== [{step['name']}] {cmd}")
    rc = subprocess.run(cmd, shell=True, cwd=ROOT, env=env).returncode
    print(f"=== [{step['name']}] exit={rc} ({time.time() - t0:.0f}s)")
    return rc


def run_stage(name: str) -> int:
    """Run every step of a stage in order; stop at the first failure and return its exit code.

    A failed step's `on_fail` command runs before the stage is reported BLOCKED. Returns 0 when all steps
    pass.

    Args:
        name: Stage key in pipeline.yaml (`ci`, `cd` or `post`).
    """
    spec = yaml.safe_load(PIPE.read_text(encoding="utf-8"))
    stage = spec["stages"][name]
    sha = git_sha()
    print(f"pipeline stage={name} sha={sha}: {stage['description']}")
    for step in stage["steps"]:
        rc = run_step(step, sha)
        if rc != 0:
            if step.get("on_fail"):
                print(f"!!! {step['name']} failed → running on_fail: {step['on_fail']}")
                subprocess.run(_py(step["on_fail"]), shell=True, cwd=ROOT)
            print(
                f"!!! stage {name} BLOCKED at step {step['name']}" + (" (gate)" if step.get("gate") else "")
            )
            return rc
    print(f"\n stage {name} GREEN")
    return 0


def prove_gate() -> int:
    """The committed proof rows must make the gate exit 1; a gate that cannot fail is no gate."""
    env = {**os.environ, "EVAL_RESULTS_DIR": str(ROOT / ".eval-results" / "proof")}
    subprocess.run(
        [sys.executable, "-m", "producery.evals.run", "_gate_red_proof", "--local"], cwd=ROOT, env=env
    )
    proof_dir = ROOT / ".eval-results" / "proof"
    gate = subprocess.run(
        [sys.executable, "-m", "producery.evals.gate", "--results", str(proof_dir)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    print(gate.stdout)
    # a missing results file also exits 1 — the proof counts only when the gate judged the proof rows RED
    judged = (
        (proof_dir / "_gate_red_proof.json").exists()
        and "_gate_red_proof" in gate.stdout
        and "RED" in gate.stdout
    )
    if gate.returncode == 1 and judged:
        print("gate self-test: RED on the proof rows — the gate can fail (OK)")
        return 0
    print(f"gate self-test FAILED: rc={gate.returncode} judged={judged} (rows must exist and be judged RED)")
    return 1


def pull_images(attempts: int = 5, wait_s: int = 30) -> int:
    """Run `docker compose pull` for the library images (postgres, redis) with retries.

    Public registries rate-limit the shared IPs of hosted runners ("toomanyrequests: Rate exceeded"), and one
    refused pull must not fail a whole run.

    Args:
        attempts: Number of pulls to try before giving up.
        wait_s: Seconds to sleep after each failed attempt.

    Returns:
        0 on the first successful pull, 1 when every attempt fails.
    """
    for attempt in range(1, attempts + 1):
        rc = subprocess.run("docker compose pull --ignore-buildable -q", shell=True, cwd=ROOT).returncode
        if rc == 0:
            return 0
        print(f"pull-images: attempt {attempt}/{attempts} failed (rc={rc}); retrying in {wait_s}s")
        time.sleep(wait_s)
    return 1


def wait_ready(timeout_s: int = 240) -> int:
    """Poll every service health URL until all answer 200 or the deadline passes; return 0 when all are ready.

    Returns 1 early when the agent answers 503 with `api_token_configured` false, and 1 at the deadline with
    the still-pending services listed.

    Args:
        timeout_s: Overall deadline in seconds.
    """
    deadline = time.time() + timeout_s
    pending = dict(SERVICES)
    while pending and time.time() < deadline:
        for name, url in list(pending.items()):
            try:
                r = httpx.get(url, timeout=5)
                if r.status_code == 200:
                    print(f"  ready: {name} {r.json()}")
                    pending.pop(name)
                elif name == "agent" and r.status_code == 503:
                    checks = (r.json() or {}).get("checks") or {}
                    if checks.get("api_token_configured") is False:
                        print(
                            "NOT READY: agent has no AGENT_API_TOKEN — run `make token` (or set the CI secret)"
                        )
                        return 1
            except Exception:
                pass
        if pending:
            time.sleep(3)
    if pending:
        print(f"NOT READY: {sorted(pending)}")
        return 1
    return 0


def pin_image_tag(tag: str, env_path: Path = ROOT / ".env") -> None:
    """Pin `IMAGE_TAG=<tag>` in .env so a later `docker compose up -d <service>` keeps the promoted images.

    Compose reads IMAGE_TAG from .env (an untagged `up` once switched the stack to
    `:local`). Nothing is written when the file does not exist.

    Args:
        tag: The image tag to pin.
        env_path: The .env file to edit.
    """
    if not env_path.exists():
        return
    text = env_path.read_text(encoding="utf-8")
    if re.search(r"^IMAGE_TAG=", text, re.M):
        text = re.sub(r"^IMAGE_TAG=.*$", f"IMAGE_TAG={tag}", text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\nIMAGE_TAG={tag}\n"
    env_path.write_text(text, encoding="utf-8")
    print(f"pinned IMAGE_TAG={tag} in {env_path.name}")


def deploy(tag: str) -> int:
    """Bring the stack up with `IMAGE_TAG` and `REVISION_ID` set to `tag`, pinning the tag on success.

    The current promoted tag (if any) is copied to `previous_tag` first so `rollback` can find it.

    Args:
        tag: The image tag to deploy.

    Returns:
        The `docker compose up` exit code.
    """
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    current = DEPLOY_DIR / "current_tag"
    if current.exists():
        shutil.copy(current, DEPLOY_DIR / "previous_tag")
    env = {**os.environ, "IMAGE_TAG": tag, "REVISION_ID": tag}
    rc = subprocess.run(
        "docker compose up -d --wait --remove-orphans", shell=True, cwd=ROOT, env=env
    ).returncode
    if rc == 0:
        pin_image_tag(tag)
    return rc


def promote(tag: str) -> int:
    """Record `tag` as the current promoted image tag and return 0.

    Args:
        tag: The image tag to record.
    """
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    (DEPLOY_DIR / "current_tag").write_text(tag, encoding="utf-8")
    print(f"promoted image tag {tag}")
    return 0


def rollback() -> int:
    """Redeploy the previously promoted tag and pin it on success.

    Returns 1 without touching the stack when no previous tag is recorded; otherwise the `docker compose up`
    exit code.
    """
    prev = DEPLOY_DIR / "previous_tag"
    if not prev.exists():
        print("rollback: no previous tag recorded; leaving the stack as-is")
        return 1
    tag = prev.read_text(encoding="utf-8").strip()
    print(f"rolling back to image tag {tag}")
    env = {**os.environ, "IMAGE_TAG": tag, "REVISION_ID": tag}
    rc = subprocess.run(
        "docker compose up -d --wait --remove-orphans", shell=True, cwd=ROOT, env=env
    ).returncode
    if rc == 0:
        pin_image_tag(tag)
    return rc


def main() -> int:
    """Dispatch the pipeline command from the command line; unknown commands print the usage and return 2."""
    p = argparse.ArgumentParser()
    p.add_argument("cmd")
    p.add_argument("--tag", default=None)
    a = p.parse_args()
    if a.cmd in ("ci", "cd", "post"):
        return run_stage(a.cmd)
    if a.cmd == "wait-ready":
        return wait_ready()
    if a.cmd == "pull-images":
        return pull_images()
    if a.cmd == "prove-gate":
        return prove_gate()
    if a.cmd == "deploy":
        return deploy(a.tag or git_sha())
    if a.cmd == "promote":
        return promote(a.tag or git_sha())
    if a.cmd == "rollback":
        return rollback()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
