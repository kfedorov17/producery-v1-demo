"""Eval-process context: the model door and the pinned prompt source the judge needs.

These are the same doors the serving path uses, so "which prompt and model judged this" is a metadata join.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from producery.model.door import ModelAccess, build_model_access
from producery.prompts.hub import PromptSource, build_prompt_source
from producery.settings import Settings, get_settings


@dataclass
class EvalContext:
    """The settings, model door and prompt source shared by every evaluator in one eval process."""

    settings: Settings
    models: ModelAccess
    prompts: PromptSource


@lru_cache(maxsize=1)
def eval_context() -> EvalContext:
    """Return the process-wide eval context, built from settings on first call and cached thereafter."""
    s = get_settings()
    return EvalContext(s, build_model_access(s), build_prompt_source(s))
