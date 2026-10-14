"""Repo-resident eval datasets (evals/datasets/*.jsonl) and their LangSmith mirror.

Datasets are mirrored to LangSmith with a content hash and the git sha, split-tagged. A row keyed by
metadata.row_id is created once; a changed row is replaced; rows carry `source_trace_id` when minted from a
production failure (the loop's "one chance to be new").
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def results_dir() -> pathlib.Path:
    """Return the directory the runner, the gate, online scoring and the suite share (`EVAL_RESULTS_DIR`)."""
    from producery.settings import get_settings

    return ROOT / get_settings().eval_results_dir


DATASETS_DIR = ROOT / "evals" / "datasets"
PREFIX = "producery"


def git_sha() -> str:
    """Return the short git sha of the repo HEAD, or `nogit` when git is unavailable or fails."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 — git on PATH is a pipeline precondition
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=True,
        ).stdout.strip()
    except Exception:
        return "nogit"


def dataset_names() -> list[str]:
    """Datasets run by --all; a leading underscore marks a self-test fixture (the gate RED proof)."""
    return sorted(p.stem for p in DATASETS_DIR.glob("*.jsonl") if not p.stem.startswith("_"))


def load_rows(name: str) -> list[dict[str, Any]]:
    """Load a dataset's JSONL rows, filling in a default `metadata.row_id` and `metadata.split`.

    Args:
        name: The dataset stem under evals/datasets (without `.jsonl`).

    Returns:
        The non-blank rows in file order; a missing row_id becomes `{name}-{index:03d}`, a missing split
        `base`.
    """
    rows = []
    for i, line in enumerate((DATASETS_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()):
        if line.strip():
            row = json.loads(line)
            row.setdefault("metadata", {}).setdefault("row_id", f"{name}-{i:03d}")
            row["metadata"].setdefault("split", "base")
            rows.append(row)
    return rows


def row_hash(row: dict[str, Any]) -> str:
    """Return the first 12 hex characters of the SHA-256 of the row's sorted-key JSON.

    Args:
        row: One dataset row.
    """
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:12]


def content_hash(rows: list[dict[str, Any]]) -> str:
    """Return the first 12 hex characters of the SHA-256 of the whole row list's sorted-key JSON.

    Args:
        rows: The dataset rows, in file order.
    """
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:12]


def sync_dataset(client: Any, name: str) -> tuple[str, int, int, int]:
    """Create or update the LangSmith dataset for `name` so it mirrors the repo file exactly.

    The dataset is created when it does not exist. Examples without a repo row are deleted; rows whose hash
    changed are deleted and re-created; unchanged rows are left alone. The dataset is then tagged
    `sha-<git sha>`; a failure to tag is printed, not raised.

    Args:
        client: A LangSmith client.
        name: The dataset stem under evals/datasets.

    Returns:
        `(dataset_name, created, replaced, removed)`, where removed counts examples that no longer have a row
        in the repo.
    """
    rows = load_rows(name)
    ds_name = f"{PREFIX}-{name}"
    from langsmith.utils import LangSmithNotFoundError

    chash = content_hash(rows)
    try:
        dataset = client.read_dataset(dataset_name=ds_name)
    except LangSmithNotFoundError:
        dataset = client.create_dataset(
            dataset_name=ds_name, description=f"{name} rows (repo evals/datasets/{name}.jsonl)"
        )
    existing = {}
    for ex in client.list_examples(dataset_id=dataset.id):
        rid = (ex.metadata or {}).get("row_id")
        if rid:
            existing[rid] = ex
    created = replaced = removed = 0
    to_create = []
    local_ids = {row["metadata"]["row_id"] for row in rows}
    for rid, ex in existing.items():  # the repo is the source of truth: an example without a row is an orphan
        if rid not in local_ids:
            client.delete_example(ex.id)
            removed += 1
    for row in rows:
        rid = row["metadata"]["row_id"]
        rh = row_hash(row)
        ex = existing.get(rid)
        if ex is not None and (ex.metadata or {}).get("row_hash") == rh:
            continue
        if ex is not None:
            client.delete_example(ex.id)
            replaced += 1
        else:
            created += 1
        to_create.append(
            {
                "inputs": row["inputs"],
                "outputs": row.get("outputs") or {},
                "metadata": {**row["metadata"], "row_hash": rh, "dataset_hash": chash, "git_sha": git_sha()},
                "split": row["metadata"].get("split", "base"),
            }
        )
    if to_create:
        client.create_examples(dataset_id=dataset.id, examples=to_create)
    try:
        client.update_dataset_tag(
            dataset_id=dataset.id,
            as_of=__import__("datetime").datetime.now(tz=__import__("datetime").UTC),
            tag=f"sha-{git_sha()}",
        )
    except Exception as e:
        print(f"  dataset tag skipped: {str(e)[:120]}")
    return ds_name, created, replaced, removed
