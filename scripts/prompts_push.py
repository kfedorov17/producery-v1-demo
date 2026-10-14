"""Push ANCHOR prompts to LangSmith Prompt Hub and pin their commit hashes.

    python scripts/prompts_push.py            # push every prompts/*.yaml whose content changed, rewrite config/prompts.yaml
    python scripts/prompts_push.py --check    # exit 1 if any local prompt differs from its pinned Hub commit

This is the ONLY writer of config/prompts.yaml. Runtime pulls `<hub_name>:<commit>`; nothing resolves `latest`.
A 409 "Nothing to commit" from push_prompt means the Hub already holds identical content: we adopt its latest
commit hash instead of failing (the friction log records why this handling exists).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from producery.prompts.hub import fingerprint, load_prompt_file, template_from_spec  # noqa: E402
from producery.settings import get_settings  # noqa: E402


def _hub_has(client, hub_name: str, commit: str) -> bool:
    """Return whether this workspace's Hub holds the pinned commit.

    False in a new organization even when the local content matches the pin, so the prompt gets pushed there
    instead of being assumed present.
    """
    try:
        client.pull_prompt(f"{hub_name}:{commit}")
        return True
    except Exception:
        return False


def commit_from_url(url: str) -> str:
    """Return the commit hash from a `push_prompt` URL (the last path segment, query string dropped).

    Args:
        url: The URL `push_prompt` returned.
    """
    # push_prompt returns https://smith.langchain.com/prompts/<name>/<commit>?organizationId=...
    tail = url.split("?")[0].rstrip("/").split("/")[-1]
    return tail


def main() -> int:
    """Push changed prompts to the Hub and rewrite config/prompts.yaml, or with `--check` report drift only.

    A prompt is reused unchanged when its fingerprint matches the pin and the pinned commit exists in this
    workspace's Hub. On a 409 conflict from `push_prompt` the Hub prompt's `last_commit_hash` is adopted; the
    conflict is re-raised when that hash is empty. In check mode, returns 1 when any prompt drifted, else 0.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    from langsmith import Client
    from langsmith.utils import LangSmithConflictError

    client = Client(api_key=settings.langsmith_api_key, api_url=settings.langsmith_endpoint)
    pins_path = settings.config_dir / "prompts.yaml"
    current = (yaml.safe_load(pins_path.read_text(encoding="utf-8")) or {}).get("pins") or {}
    new_pins: dict[str, dict[str, str]] = {}
    drift = False

    for path in sorted(settings.prompts_dir.glob("*.yaml")):
        spec = load_prompt_file(path)
        name, hub_name = spec["name"], spec["hub_name"]
        fp = fingerprint(spec)
        prev = current.get(name) or {}
        if (
            prev.get("fingerprint") == fp
            and prev.get("commit")
            and _hub_has(client, hub_name, prev["commit"])
        ):
            new_pins[name] = {"hub_name": hub_name, "commit": prev["commit"], "fingerprint": fp}
            print(f"  = {name}: unchanged ({hub_name}:{prev['commit']})")
            continue
        if prev.get("fingerprint") == fp and prev.get("commit"):
            # same content, but the pinned commit is not in THIS workspace's Hub (a new organization): push it
            print(f"  > {name}: pin {hub_name}:{prev['commit']} not found in this workspace — pushing")
        drift = True
        if args.check:
            print(f"  ! {name}: local prompt differs from pin")
            continue
        template = template_from_spec(spec)
        try:
            url = client.push_prompt(
                hub_name,
                object=template,
                description=spec.get("description", ""),
                tags=["producery", "anchor", f"agent:{name}"],
                commit_description=f"fingerprint {fp}",
            )
            commit = commit_from_url(url)
        except LangSmithConflictError:
            prompt = client.get_prompt(hub_name)
            commit = str(getattr(prompt, "last_commit_hash", "") or "")
            if not commit:
                raise
            print(f"  ~ {name}: Hub already holds this content; adopting {commit[:8]}")
        new_pins[name] = {"hub_name": hub_name, "commit": commit, "fingerprint": fp}
        print(f"  + {name}: pushed → {hub_name}:{commit}")

    if args.check:
        return 1 if drift else 0

    header = (
        "# Prompt pins. Written ONLY by scripts/prompts_push.py; read at startup by producery.prompts.hub.\n"
        "# key = local prompt name (prompts/<name>.yaml); hub_name = LangSmith Hub prompt; commit = immutable commit hash.\n"
    )
    pins_path.write_text(header + yaml.safe_dump({"pins": new_pins}, sort_keys=True), encoding="utf-8")
    print(f"pins written: {pins_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
