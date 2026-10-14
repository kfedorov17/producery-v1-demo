"""Build the drafter deep agent and its subagents from `config/agents.yaml`.

Config names, code implements: tool names are resolved against the explicit `ToolRegistry` (an ungranted tool is a
schema the agent never sees; an unknown name fails at startup) and prompts against the pinned Hub source. Nothing
here hand-rolls what deepagents and LangChain provide.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from langchain.agents.middleware import ModelFallbackMiddleware, ModelRetryMiddleware, ToolCallLimitMiddleware
from langchain_core.language_models.chat_models import BaseChatModel

from producery.agents.config import AgentDef, AgentsConfig
from producery.agents.middleware import RetrievalFirstMiddleware
from producery.contracts import DraftResponse
from producery.model.door import ModelAccess
from producery.prompts.hub import PromptSource
from producery.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Filesystem tools are not granted to any agent in this solution; the drafter needs no scratch files.
RETRIEVAL_TOOLS = frozenset({"sql_query", "documents_search"})  # the tools whose call must come first
_EXCLUDED_BUILTINS = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}
)


def _register_profile(model: BaseChatModel) -> None:
    """Register a deepagents HarnessProfile for the model door's models.

    The profile disables the general-purpose subagent and the filesystem tools. It is keyed exactly like
    deepagents resolves pre-built models (provider:identifier); nothing is registered when either part is
    unknown.

    Args:
        model: The chat model whose provider and identifier form the registration key.
    """
    from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile
    from deepagents._models import get_model_identifier, get_model_provider

    profile = HarnessProfile(
        excluded_tools=_EXCLUDED_BUILTINS,
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    )
    provider, identifier = get_model_provider(model), get_model_identifier(model)
    # exact key only: a provider-wide registration would leak these exclusions into any other deepagents agent
    keys = [f"{provider}:{identifier}"] if provider and identifier else []
    for key in keys:
        register_harness_profile(key, profile)
    logger.info("harness_profile_registered keys=%s", keys)


class AgentFactory:
    """Build the drafter deep agent and its subagents from the config, model door, prompts and tools."""

    def __init__(
        self, config: AgentsConfig, models: ModelAccess, prompts: PromptSource, tools: ToolRegistry
    ) -> None:
        """Store the collaborators; nothing is built until `build_drafter` is called.

        Args:
            config: The parsed config/agents.yaml.
            models: The model door that resolves a tier to a chat model and its fallbacks.
            prompts: The pinned prompt source.
            tools: The registry that resolves granted tool names to tool objects.
        """
        self.config = config
        self.models = models
        self.prompts = prompts
        self.tools = tools

    def validate(self) -> None:
        """Fail fast at startup: every granted tool must exist in the registry; every prompt must be pinned."""
        for a in self.config.agents.values():
            self.tools.resolve(list(a.tools), agent=a.name)
            self.prompts.get(a.prompt)
        logger.info("agent_config_valid agents=%s", sorted(self.config.agents))

    def model_for(self, agent: AgentDef) -> BaseChatModel:
        """Return the chat model the model door assigns to the agent's identity and tier.

        Args:
            agent: The agent definition whose `identity` and `model_tier` are looked up.
        """
        return self.models.for_agent(agent.identity, agent.model_tier)

    def fallback_middleware(self, agent: AgentDef) -> list[Any]:
        """Return the declared fallback chain wrapped in LangChain's ModelFallbackMiddleware.

        Args:
            agent: The agent definition whose fallbacks are looked up on the model door.

        Returns:
            A single-item list holding the middleware, or an empty list when no fallbacks are declared
            (Gateway mode).
        """
        fallbacks = self.models.fallbacks_for(agent.identity, agent.model_tier)
        return [ModelFallbackMiddleware(fallbacks[0], *fallbacks[1:])] if fallbacks else []

    def _subagent_spec(self, name: str) -> dict[str, Any]:
        a = self.config.agents[name]
        model = self.model_for(a)
        limit = int(a.guardrails.get("max_tool_calls", 8))
        return {
            "name": a.name,
            "description": a.scope,  # the routing contract the parent sees
            "system_prompt": self.prompts.get(a.prompt).system,
            "tools": self.tools.resolve(list(a.tools), agent=a.name),
            "model": model,
            "middleware": [
                *self.fallback_middleware(a),
                ModelRetryMiddleware(max_retries=1, jitter=True, on_failure="error"),
                ToolCallLimitMiddleware(run_limit=limit, exit_behavior="end"),
                # a data subagent's first move is a retrieval tool call — enforced, not requested
                *(
                    [RetrievalFirstMiddleware(retrieval_tools=[n for n in a.tools if n in RETRIEVAL_TOOLS])]
                    if any(n in RETRIEVAL_TOOLS for n in a.tools)
                    else []
                ),
            ],
        }

    def build_drafter(self) -> Any:
        """Build the `drafter` deep agent with its subagents, middleware and `DraftResponse` output schema.

        Harness profiles are registered for the drafter's model and every subagent's model first. The returned
        runnable carries the `recursion_limit` from the drafter's guardrails (default 40).

        Returns:
            The compiled deep agent, configured with its recursion limit.
        """
        from deepagents import create_deep_agent

        a = self.config.agents["drafter"]
        model = self.model_for(a)
        for sub in a.subagents:
            _register_profile(self.model_for(self.config.agents[sub]))
        _register_profile(model)
        subagents = [self._subagent_spec(s) for s in a.subagents]
        agent = create_deep_agent(
            model=model,
            tools=self.tools.resolve(list(a.tools), agent=a.name),  # [] — the drafter gets no data tools
            system_prompt=self.prompts.get(a.prompt).system,
            subagents=cast(Any, subagents),
            middleware=cast(
                Any,
                [
                    *self.fallback_middleware(a),
                    ModelRetryMiddleware(max_retries=1, jitter=True, on_failure="error"),
                    ToolCallLimitMiddleware(
                        run_limit=int(a.guardrails.get("max_tool_calls", 8)), exit_behavior="end"
                    ),
                ],
            ),
            response_format=DraftResponse,
            name="drafter",
        )
        return agent.with_config({"recursion_limit": int(a.guardrails.get("recursion_limit", 40))})

    def describe(self) -> dict[str, Any]:
        """Return a JSON-friendly summary of every agent: identity, kind, reason, tier, prompt pin, tools."""
        return {
            name: {
                "identity": a.identity,
                "kind": a.kind,
                "reason": a.reason,
                "model_tier": a.model_tier,
                "prompt": f"{self.prompts.get(a.prompt).hub_name}:{self.prompts.get(a.prompt).commit}",
                "tools": list(a.tools),
                "subagents": list(a.subagents),
            }
            for name, a in self.config.agents.items()
        }
