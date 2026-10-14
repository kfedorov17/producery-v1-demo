"""Resolve every prompt from the LangSmith Prompt Hub by its pinned commit.

Pulls happen once at startup into memory; the request path never touches the network. A pin that cannot be resolved
fails readiness: the service never serves an unpinned prompt.

Hub outage at startup: when a pull fails and the local `prompts/<name>.yaml` carries the fingerprint the pin was
pushed from, the local copy is the pinned content byte for byte, so the service starts from it and says so
(`/ready` → `prompts_source: local-fallback`). A fingerprint mismatch still fails.

`LocalPromptSource` reads `prompts/<name>.yaml` directly and is allowed only for unit tests and the push script.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml
from langchain_core.prompts import ChatPromptTemplate

from producery.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PinnedPrompt:
    """A prompt resolved to one Hub commit, with its system text and compiled template."""

    name: str
    hub_name: str
    commit: str
    system: str
    template: ChatPromptTemplate

    @property
    def ref(self) -> str:
        """Return the ``hub_name:commit`` reference."""
        return f"{self.hub_name}:{self.commit}"


class PromptSource(Protocol):
    """Interface a prompt source exposes to the agents and to readiness."""

    def get(self, name: str) -> PinnedPrompt:
        """Return the pinned prompt by name.

        Args:
            name: Prompt name.
        """
        ...

    def pins(self) -> dict[str, str]:
        """Return prompt name to commit."""
        ...

    def ready(self) -> bool:
        """Return whether every prompt is loaded."""
        ...

    def sources(self) -> dict[str, str]:
        """Return prompt name to where it was loaded from (hub, local-fallback or local)."""
        ...


def load_prompt_file(path: Path) -> dict[str, Any]:
    """Load a prompt YAML file and check that it has name, hub_name, system and human.

    Args:
        path: The prompt file.

    Returns:
        The raw mapping.

    Raises:
        ValueError: When one of the required keys is missing.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("name", "hub_name", "system", "human"):
        if key not in raw:
            raise ValueError(f"{path}: missing {key!r}")
    return raw


def fingerprint(spec: dict[str, Any]) -> str:
    """Return the content fingerprint of a prompt file (system + human).

    Written into the pin by scripts/prompts_push.py and the proof that a local file equals the pinned Hub
    commit.

    Args:
        spec: The loaded prompt mapping.
    """
    return hashlib.sha256(
        json.dumps({"s": spec["system"], "h": spec["human"]}, sort_keys=True).encode()
    ).hexdigest()[:16]


def template_from_spec(spec: dict[str, Any]) -> ChatPromptTemplate:
    """Stable system prefix first (cacheable), one volatile human slot after it."""
    return ChatPromptTemplate.from_messages(
        [("system", spec["system"]), ("human", spec["human"])],
        template_format=spec.get("template_format", "mustache"),
    )


def _system_text(template: ChatPromptTemplate) -> str:
    first = template.messages[0]
    prompt = getattr(first, "prompt", None)
    return str(getattr(prompt, "template", "") or "")


class HubPromptSource:
    """Prompt source that pulls every pinned commit from the LangSmith Hub once, at ``load``."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        """Create an empty, not-ready source; nothing is pulled until ``load``.

        Args:
            settings: Application settings (pins file, Hub credentials, fallback flag).
            client: An existing LangSmith client, or None to build one in ``load``.
        """
        self._s = settings
        self._client = client
        self._cache: dict[str, PinnedPrompt] = {}
        self._pins: dict[str, dict[str, str]] = {}
        self._sources: dict[str, str] = {}
        self._ready = False

    def _local_fallback(self, name: str, pin: dict[str, str], error: Exception) -> ChatPromptTemplate:
        """The local prompt file, only when its fingerprint is the one the pin was pushed from."""
        if not self._s.prompts_local_fallback:
            raise error
        path = self._s.prompts_dir / f"{name}.yaml"
        if not path.exists():
            raise error
        spec = load_prompt_file(path)
        fp = fingerprint(spec)
        if not pin.get("fingerprint") or fp != pin["fingerprint"]:
            raise RuntimeError(
                f"prompt {name!r}: Hub unreachable ({str(error)[:120]}) and local file fingerprint {fp} != "
                f"pinned {pin.get('fingerprint')} — refusing to serve an unpinned prompt"
            ) from error
        logger.warning(
            "prompt_local_fallback name=%s commit=%s fingerprint=%s hub_error=%s",
            name,
            pin.get("commit"),
            fp,
            str(error)[:160],
        )
        return template_from_spec(spec)

    def load(self) -> None:
        """Pull every pin in ``config/prompts.yaml`` into memory and mark the source ready.

        A pull that fails falls back to the local file when its fingerprint matches the pin (recorded as
        source ``local-fallback``); otherwise the error propagates and the service never becomes ready.

        Raises:
            RuntimeError: When there are no pins, a pin has no commit, the fallback fingerprint does not
                match, or a pulled object is not a ChatPromptTemplate.
            Exception: The pull error itself when the local fallback is disabled or the local file is missing.
        """
        raw = yaml.safe_load((self._s.config_dir / "prompts.yaml").read_text(encoding="utf-8")) or {}
        pins: dict[str, dict[str, str]] = raw.get("pins") or {}
        if not pins:
            raise RuntimeError("config/prompts.yaml has no pins — run scripts/prompts_push.py first")
        client = self._client
        if client is None:
            from langsmith import Client

            client = Client(api_key=self._s.langsmith_api_key, api_url=self._s.langsmith_endpoint)
        for name, pin in pins.items():
            commit = str(pin.get("commit") or "")
            hub_name = str(pin.get("hub_name") or f"{self._s.prompt_hub_prefix}-{name}")
            if not commit:
                raise RuntimeError(f"prompt {name!r} has no pinned commit (unpushed)")
            try:
                template = client.pull_prompt(f"{hub_name}:{commit}")
                source = "hub"
            except Exception as e:
                template = self._local_fallback(name, pin, e)
                source = "local-fallback"
            if not isinstance(template, ChatPromptTemplate):
                raise RuntimeError(f"prompt {hub_name}:{commit} is not a ChatPromptTemplate")
            self._cache[name] = PinnedPrompt(name, hub_name, commit, _system_text(template), template)
            self._sources[name] = source
            logger.info("prompt_pinned name=%s ref=%s:%s source=%s", name, hub_name, commit, source)
        self._pins = pins
        self._ready = True

    def get(self, name: str) -> PinnedPrompt:
        """Return the cached prompt by name (KeyError when it is not loaded).

        Args:
            name: Prompt name.
        """
        return self._cache[name]

    def pins(self) -> dict[str, str]:
        """Return prompt name to pinned commit for the loaded prompts."""
        return {n: p.commit for n, p in self._cache.items()}

    def sources(self) -> dict[str, str]:
        """Return prompt name to ``hub`` or ``local-fallback``."""
        return dict(self._sources)

    def ready(self) -> bool:
        """Return True once ``load`` has completed."""
        return self._ready


class LocalPromptSource:
    """Unit tests only: refuses to run outside PRODUCERY_ENV=test (the push script reads prompt files directly)."""

    def __init__(self, settings: Settings) -> None:
        """Read every ``prompts/*.yaml`` file into memory with commit ``local``.

        Args:
            settings: Application settings; ``producery_env`` must be ``test``.

        Raises:
            RuntimeError: When ``producery_env`` is not ``test``.
        """
        if settings.producery_env != "test":
            raise RuntimeError("LocalPromptSource is only permitted in tests (PRODUCERY_ENV=test)")
        self._dir = settings.prompts_dir
        self._prefix = settings.prompt_hub_prefix
        self._cache: dict[str, PinnedPrompt] = {}
        for path in sorted(self._dir.glob("*.yaml")):
            spec = load_prompt_file(path)
            template = template_from_spec(spec)
            self._cache[spec["name"]] = PinnedPrompt(
                spec["name"], spec["hub_name"], "local", spec["system"], template
            )

    def get(self, name: str) -> PinnedPrompt:
        """Return the prompt by name (KeyError when there is no such file).

        Args:
            name: Prompt name.
        """
        return self._cache[name]

    def pins(self) -> dict[str, str]:
        """Return prompt name to the literal commit ``local``."""
        return {n: "local" for n in self._cache}

    def sources(self) -> dict[str, str]:
        """Return prompt name to the literal source ``local``."""
        return {n: "local" for n in self._cache}

    def ready(self) -> bool:
        """Return True when at least one prompt file was read."""
        return bool(self._cache)


def build_prompt_source(settings: Settings) -> PromptSource:
    """Build the prompt source selected by ``settings.prompts_source``.

    ``hub`` builds and loads a ``HubPromptSource`` (pull failures propagate); any other value builds a
    ``LocalPromptSource``.

    Args:
        settings: Application settings.
    """
    if settings.prompts_source == "hub":
        src = HubPromptSource(settings)
        src.load()
        return src
    return LocalPromptSource(settings)
