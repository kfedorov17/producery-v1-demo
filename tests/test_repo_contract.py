"""Repository contracts.

The env example matches Settings, langgraph.json matches the app, declared dependencies are the imported ones, and
configuration is read through Settings only.
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
import sys
import tomllib
from pathlib import Path

import yaml

from producery.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
DERIVED = {"config_dir", "prompts_dir", "revision_id"}  # baked or path-derived, not env


def test_env_example_covers_every_setting_and_requires_the_token():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    keys = set(re.findall(r"^#?([A-Z_]+)=", text, re.M))
    fields = {name.upper() for name in Settings.model_fields} - {d.upper() for d in DERIVED}
    assert fields <= keys, f".env.example misses {sorted(fields - keys)}"
    assert (
        re.search(r"^AGENT_API_TOKEN=", text, re.M) and "REQUIRED" in text.split("AGENT_API_TOKEN=")[0][-400:]
    )
    assert "REVISION_ID=" not in text.replace("# REVISION_ID", "")  # baked at build, never in env


def test_langgraph_json_matches_the_application():
    cfg = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    assert cfg["dependencies"] == ["."]
    for name, ref in cfg["graphs"].items():
        path, attr = ref.split(":")
        src = (ROOT / path.lstrip("./")).read_text(encoding="utf-8")
        assert re.search(rf"^{attr} = ", src, re.M), (
            f"{name}: {ref} must be a module-level compiled graph (loaded once at startup)"
        )
    index = cfg["store"]["index"]
    assert index["dims"] == Settings(producery_env="test", _env_file=None).embed_dims == 384
    path, attr = index["embed"].split(":")
    fn = getattr(importlib.import_module(path.lstrip("./").replace("/", ".").removesuffix(".py")), attr)
    assert inspect.iscoroutinefunction(fn), "store embed must be the app's async embedding function"
    assert cfg["env"] == "./.env" and ".env\n" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_declared_dependencies_are_the_imported_ones():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    declared = {re.split(r"[><=\[]", d)[0].lower().replace("-", "_") for d in deps}
    import ast

    imported: set[str] = (
        set()
    )  # real import statements only (a regex over source text also matches SQL in strings)
    for path in (ROOT / "producery").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
    std = set(sys.stdlib_module_names) | {"producery"}
    third = {i for i in imported if i not in std}
    alias = {"yaml": "pyyaml", "psycopg_pool": "psycopg", "langgraph": "langgraph", "langchain": "langchain"}
    provided = declared | {
        "langgraph",
        "langchain",
    }  # langgraph.checkpoint.postgres ships in langgraph_checkpoint_postgres
    missing = {i for i in third if alias.get(i, i) not in provided and i not in {"langchain_core"}}
    assert missing == set(), f"imported but not declared: {sorted(missing)}"
    cli_only = {"uvicorn"}  # started by the Dockerfile CMD, never imported
    unused = {
        d
        for d in declared
        if d not in imported
        and d not in cli_only
        and d
        not in {"langgraph_checkpoint_postgres", "pyyaml"}  # imported as langgraph.checkpoint.postgres / yaml
    }
    assert unused == set(), f"declared but never imported: {sorted(unused)}"


def test_image_pins_config_and_prompt_dirs():
    """The package is installed into site-packages inside the image; config must not be resolved from the module path."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "CONFIG_DIR=/app/config" in dockerfile and "PROMPTS_DIR=/app/prompts" in dockerfile


def test_no_ad_hoc_environment_reads_outside_the_settings_module():
    allowed = {
        "producery/settings.py",
        "producery/executor/server.py",
        "producery/evals/run.py",
    }  # executor is stdlib-only; run.py: gate self-test override
    offenders = []
    for p in (ROOT / "producery").rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel in allowed:
            continue
        if re.search(r"os\.environ|os\.getenv", p.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == ["producery/mcp_exec/executors.py"], (
        offenders
    )  # sandbox-side snippet reads PRODUCERY_INPUTS inside the VM


def test_makefile_is_a_small_well_formed_makefile():
    """Keep the Makefile small and shaped like one.

    A scripted edit once inserted a recipe between every character (2.7 MB, no usable target) and nothing noticed
    because the workstation has no `make`.
    """
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert len(text) < 50_000, f"Makefile is {len(text)} bytes — corrupted?"
    lines = text.splitlines()
    targets = {line.split(":")[0] for line in lines if line and line[0] not in " \t#" and ":" in line}
    for required in ("setup", "token", "up", "down", "test", "evals", "prove-gate", "pipeline-ci", "web-e2e"):
        assert required in targets, f"Makefile lost target {required}"
    shape = re.compile(r"^(	|#|[A-Za-z0-9_.\-]+\s*:|[A-Za-z_]+\s*[?:]?=|ifeq|ifneq|else|endif|\.PHONY)")
    odd = [line for line in lines if line.strip() and not shape.match(line)]
    assert not odd, f"lines make would reject (missing separator): {odd[:3]}"


def test_compose_refuses_an_unset_image_tag_and_env_example_pins_one():
    """Compose requires IMAGE_TAG and .env.example pins one.

    `${IMAGE_TAG:-local}` let an untagged `docker compose up` switch the running stack to another image. The variable
    must be required, and .env.example must carry it so the quickstart still works.
    """
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "IMAGE_TAG:-" not in compose
    assert compose.count("${IMAGE_TAG:?") == 7
    assert re.search(r"^IMAGE_TAG=local", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M)


def test_schema_carries_the_hot_path_indexes_and_the_expired_status():
    """Critical review Q9 / Q11."""
    schema = (ROOT / "producery" / "store" / "schema.sql").read_text(encoding="utf-8")
    assert "inbox_status_received_idx on inbox (status, received_at)" in schema
    assert "audit_log_thread_idx on audit_log (thread_id, id)" in schema
    assert "'executed','expired'" in schema and "drop constraint if exists proposals_status_check" in schema


def test_langgraph_json_agrees_with_retention_and_agents_config():
    """The LangSmith Deployment manifest repeats three values the config files own; they must not drift."""
    cfg = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    retention = yaml.safe_load((ROOT / "config" / "retention.yaml").read_text(encoding="utf-8"))
    agents = yaml.safe_load((ROOT / "config" / "agents.yaml").read_text(encoding="utf-8"))
    assert (
        cfg["checkpointer"]["ttl"]["default_ttl"] == retention["thread_classes"]["crew"]["ttl_days"] * 24 * 60
    )
    assert cfg["store"]["ttl"]["default_ttl"] is None and retention["memory"]["semantic"] is None
    assert list(cfg["graphs"]) == [agents["graph"]["name"]]
