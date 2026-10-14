"""Provide the one model-access door every agent calls through.

Application code holds one client per agent and tier pointed at the LangSmith LLM Gateway plus an agent identity.
No provider SDKs, no provider keys and no routing logic live here: routing, fallbacks and provider credentials are
Gateway configuration (`config/models.yaml` mirrors the Gateway fallback-chain object).

Two implementations of the same `ModelAccess` protocol:

* `GatewayModelAccess`: the design target. An OpenAI-compatible client (`ChatOpenAI`) with `base_url` set to the
  Gateway and `api_key` the LangSmith workspace key. Model id = the tier's Gateway id.
* `LangChainFallbackModelAccess`: the flagged interim door, selected only by `MODEL_ACCESS_MODE=langchain`. It
  consumes the same tier and fallback YAML through LangChain-native `init_chat_model(...).with_fallbacks(...)`. There
  is still no routing code: the chain is declared, not computed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml
from langchain_core.language_models.chat_models import BaseChatModel

from producery.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tier:
    """One model tier from ``config/models.yaml``: primary model, declared fallbacks and sampling limits."""

    name: str
    model: str
    fallbacks: tuple[str, ...] = ()
    triggers: tuple[int, ...] = (429, 500, 502, 503, 504)
    temperature: float = 0.0
    max_tokens: int = 4096


@dataclass(frozen=True)
class ModelPolicy:
    """The tiers and the agent header declared in ``config/models.yaml``."""

    tiers: dict[str, Tier]
    agent_header: str = "x-producery-agent"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ModelPolicy:
        """Load the policy from a YAML file.

        Args:
            path: The models YAML file.

        Returns:
            The policy; ``extra`` is left empty.

        Raises:
            ValueError: When the file defines no tiers.
        """
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tiers = {
            name: Tier(
                name=name,
                model=str(spec["model"]),
                fallbacks=tuple(spec.get("fallbacks", [])),
                triggers=tuple(int(t) for t in spec.get("triggers", [429, 500, 502, 503, 504])),
                temperature=float(spec.get("temperature", 0.0)),
                max_tokens=int(spec.get("max_tokens", 4096)),
            )
            for name, spec in (raw.get("tiers") or {}).items()
        }
        if not tiers:
            raise ValueError(f"{path}: no tiers defined")
        return cls(
            tiers=tiers, agent_header=str((raw.get("gateway") or {}).get("agent_header", "x-producery-agent"))
        )

    def tier(self, name: str) -> Tier:
        """Return a tier by name.

        Args:
            name: Tier name.

        Raises:
            KeyError: When the tier is not defined (the message lists the defined tiers).
        """
        try:
            return self.tiers[name]
        except KeyError as e:
            raise KeyError(f"unknown model tier {name!r}; defined: {sorted(self.tiers)}") from e


class ModelAccess(Protocol):
    """The single door.

    `for_agent` returns the primary chat model for an agent identity + tier; `fallbacks_for` returns the
    declared fallback models (empty when the Gateway owns failover). Callers attach them with LangChain-native
    mechanisms only: `Runnable.with_fallbacks` for single calls, `ModelFallbackMiddleware` inside agents —
    never routing logic of their own.
    """

    def for_agent(self, agent_id: str, tier: str) -> BaseChatModel:
        """Return the primary chat model for an agent identity and tier.

        Args:
            agent_id: Agent identity sent with every call.
            tier: Tier name from the policy.
        """
        ...

    def fallbacks_for(self, agent_id: str, tier: str) -> list[BaseChatModel]:
        """Return the declared fallback models for an agent identity and tier.

        Args:
            agent_id: Agent identity sent with every call.
            tier: Tier name from the policy.
        """
        ...

    def describe(self) -> dict[str, Any]:
        """Return a JSON-serializable description of the door for ``/agents`` and ``/ready``."""
        ...

    def configured(self) -> bool:
        """Return whether the door has the settings it needs to reach a model."""
        ...


class GatewayModelAccess:
    """LangSmith LLM Gateway client. One base URL, one key, per-agent identity header."""

    def __init__(self, settings: Settings, policy: ModelPolicy) -> None:
        """Keep settings and policy; one client per (agent, tier) is built on first use.

        Args:
            settings: Application settings (Gateway base URL, LangSmith key, timeout, label).
            policy: The loaded tier policy.
        """
        self._s = settings
        self._p = policy
        self._clients: dict[
            tuple[str, str], BaseChatModel
        ] = {}  # one client per (agent, tier) per process (R20)

    def configured(self) -> bool:
        """Return True when both the LangSmith API key and the Gateway base URL are set."""
        return bool(self._s.langsmith_api_key and self._s.langsmith_gateway_base_url)

    def for_agent(self, agent_id: str, tier: str) -> BaseChatModel:
        """Return the cached ChatOpenAI client for (agent, tier), building it on first use.

        Args:
            agent_id: Agent identity sent in the agent header and tags.
            tier: Tier name from the policy.

        Raises:
            KeyError: When the tier is not defined.
        """
        key = (agent_id, tier)
        if key not in self._clients:
            self._clients[key] = self._build(agent_id, tier)
        return self._clients[key]

    def _build(self, agent_id: str, tier: str) -> BaseChatModel:
        from langchain_openai import (
            ChatOpenAI,  # OpenAI-compatible wire format; the Gateway translates upstream
        )

        t = self._p.tier(tier)
        return ChatOpenAI(
            model=t.model,
            base_url=self._s.langsmith_gateway_base_url,
            api_key=self._s.langsmith_api_key or "unset",  # type: ignore[arg-type]
            temperature=t.temperature,
            max_completion_tokens=t.max_tokens,
            timeout=self._s.model_timeout_s,
            max_retries=1,  # the Gateway owns failover; one transport retry only
            use_responses_api=False,
            default_headers={self._p.agent_header: agent_id, "x-producery-tier": tier},
            stream_usage=True,
            tags=[
                f"agent:{agent_id}",
                f"tier:{tier}",
            ],  # LangSmith filter: the analyst no longer hides under agent:drafter
        )

    def fallbacks_for(self, agent_id: str, tier: str) -> list[BaseChatModel]:
        """Return an empty list: the Gateway's fallback chain owns failover.

        Args:
            agent_id: Agent identity (unused).
            tier: Tier name (unused).
        """
        return []  # the Gateway's fallback chain owns failover

    def describe(self) -> dict[str, Any]:
        """Return mode ``gateway``, label, base URL, configured flag and the tiers with their fallbacks."""
        return {
            "mode": "gateway",
            "label": self._s.model_door_label,
            "base_url": self._s.langsmith_gateway_base_url,
            "configured": self.configured(),
            "tiers": {
                k: {"model": v.model, "fallbacks": list(v.fallbacks)} for k, v in self._p.tiers.items()
            },
        }


class LangChainFallbackModelAccess:
    """Interim door (flagged). Same YAML, LangChain-native fallbacks, provider keys from env only."""

    def __init__(self, settings: Settings, policy: ModelPolicy) -> None:
        """Keep settings and policy; one client per (model, agent, tier) is built on first use.

        Args:
            settings: Application settings (optional Gateway base URL, LangSmith key, timeout, label).
            policy: The loaded tier policy.
        """
        self._s = settings
        self._p = policy
        self._clients: dict[tuple[str, str, str], BaseChatModel] = {}  # (model, agent, tier) → client (R20)

    @staticmethod
    def _spec(model_id: str) -> str:
        # Gateway ids are "<provider>/<model>"; init_chat_model wants "<provider>:<model>".
        provider, _, name = model_id.partition("/")
        provider = {"google": "google_genai"}.get(provider, provider)
        return f"{provider}:{name}"

    def configured(self) -> bool:
        """Return True when a Gateway base URL is set."""
        return bool(self._s.langsmith_gateway_base_url)

    def _client(self, model_id: str, t: Tier, agent_id: str, tier: str) -> BaseChatModel:
        key = (model_id, agent_id, tier)
        if key not in self._clients:
            self._clients[key] = self._new_client(model_id, t, agent_id, tier)
        return self._clients[key]

    def _new_client(self, model_id: str, t: Tier, agent_id: str, tier: str) -> BaseChatModel:
        """Build one OpenAI-compatible client per chain member.

        With a base URL configured (the LangSmith Gateway or the flagged CLI bridge) every member goes to that
        endpoint; without one, provider SDKs via init_chat_model.
        """
        if self._s.langsmith_gateway_base_url:
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=model_id,
                base_url=self._s.langsmith_gateway_base_url,
                api_key=self._s.langsmith_api_key or "unset",  # type: ignore[arg-type]
                temperature=t.temperature,
                max_completion_tokens=t.max_tokens,
                timeout=self._s.model_timeout_s,
                max_retries=0,  # failover is the chain's job, not the client's
                use_responses_api=False,
                default_headers={self._p.agent_header: agent_id, "x-producery-tier": tier},
                stream_usage=True,
                tags=[
                    f"agent:{agent_id.split('@')[0]}",
                    f"tier:{tier}",
                ],  # same tags as the Gateway door
            )
        from langchain.chat_models import init_chat_model

        return init_chat_model(
            self._spec(model_id),
            temperature=t.temperature,
            max_tokens=t.max_tokens,
            timeout=self._s.model_timeout_s,
            tags=[f"agent:{agent_id.split('@')[0]}", f"tier:{tier}"],
        )

    def for_agent(self, agent_id: str, tier: str) -> BaseChatModel:
        """Return the cached client for the tier's primary model, building it on first use.

        Args:
            agent_id: Agent identity sent in the agent header and tags.
            tier: Tier name from the policy.

        Raises:
            KeyError: When the tier is not defined.
        """
        t = self._p.tier(tier)
        return self._client(t.model, t, agent_id, tier)

    def fallbacks_for(self, agent_id: str, tier: str) -> list[BaseChatModel]:
        """Return one client per declared fallback model of the tier, in declaration order.

        Args:
            agent_id: Agent identity sent in the agent header and tags.
            tier: Tier name from the policy.

        Raises:
            KeyError: When the tier is not defined.
        """
        t = self._p.tier(tier)
        return [self._client(m, t, agent_id, tier) for m in t.fallbacks]

    def describe(self) -> dict[str, Any]:
        """Return mode ``langchain-interim``, label, base URL, configured flag and tiers with fallbacks."""
        return {
            "mode": "langchain-interim",
            "label": self._s.model_door_label,
            "base_url": self._s.langsmith_gateway_base_url,
            "configured": self.configured(),
            "tiers": {
                k: {"model": v.model, "fallbacks": list(v.fallbacks)} for k, v in self._p.tiers.items()
            },
        }


def build_model_access(settings: Settings, policy: ModelPolicy | None = None) -> ModelAccess:
    """Build the model door selected by ``settings.model_access_mode``.

    ``gateway`` returns ``GatewayModelAccess``; any other value logs a warning and returns the interim
    ``LangChainFallbackModelAccess``.

    Args:
        settings: Application settings.
        policy: A loaded policy, or None to load ``models.yaml`` from ``settings.config_dir``.
    """
    policy = policy or ModelPolicy.load(settings.config_dir / "models.yaml")
    if settings.model_access_mode == "gateway":
        return GatewayModelAccess(settings, policy)
    logger.warning("model door running in INTERIM langchain mode (flagged deviation from the Gateway design)")
    return LangChainFallbackModelAccess(settings, policy)
