"""12-factor configuration. Every application module receives a `Settings` instance.

The stdlib-only executor daemon, the host-side bridge and the host scripts are the documented exceptions that read
the environment directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_root() -> Path:
    """Return the directory that holds `config/` and `prompts/`.

    In a checkout that is the parent of the package; in an image the package is installed into site-packages, so
    fall back to the working directory (/app) that carries the config. CONFIG_DIR / PROMPTS_DIR env always win.
    """
    here = Path(__file__).resolve().parent.parent
    if (here / "config").is_dir():
        return here
    cwd = Path.cwd()
    if (cwd / "config").is_dir():
        return cwd
    return here


REPO_ROOT = _repo_root()


class Settings(BaseSettings):
    """Every setting the services read, loaded from the environment (and `.env`) by pydantic-settings.

    Groups: identity, LangSmith, the model door, prompts, stores, the service mesh, the code executor, the
    host-side evaluation harness, embeddings, timeouts and the config directories. Unknown variables are ignored.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # identity
    producery_env: Literal["local", "ci", "prod", "test"] = "local"
    agency_id: str = "lakeview"
    revision_id: str = "dev"

    # langsmith
    langsmith_tracing: bool = True
    langsmith_api_key: str = ""
    langsmith_project: str = "producery"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_mask_pii: bool = True  # anonymize e-mail local parts, phones, SSNs, sender names in traces

    # model door
    model_access_mode: Literal["gateway", "langchain"] = "gateway"
    langsmith_gateway_base_url: str = "https://gateway.smith.langchain.com/v1"
    agent_api_token: str = ""  # service token for every agent-API route except the probes
    model_door_label: str = (
        "gateway"  # what traces call the door; e.g. "CLI bridge (interim)" when a bridge stands in
    )

    # prompts
    prompts_source: Literal["hub", "local"] = "hub"
    prompt_hub_prefix: str = "producery"
    prompts_local_fallback: bool = (
        True  # Hub unreachable at startup → local file iff its fingerprint matches the pin
    )

    # stores
    database_url: str = "postgresql://producery:producery@localhost:5432/producery"
    database_readonly_role: str = "producery_readonly"
    redis_url: str = "redis://localhost:6379/0"

    # mesh
    agent_url: str = "http://localhost:8000"
    mcp_data_url: str = "http://localhost:8081/mcp"
    mcp_exec_url: str = "http://localhost:8082/mcp"
    executor_url: str = "http://localhost:9000"

    # code executor behind run_code: sealed (compose) | langsmith (Sandboxes)
    code_executor: Literal["sealed", "langsmith"] = "sealed"

    # evaluation harness (host side)
    eval_agent_url: str = "http://localhost:8010"
    eval_mcp_data_url: str = "http://localhost:8091/mcp"
    eval_mcp_exec_url: str = "http://localhost:8092/mcp"
    eval_thread_class: str = "eval"
    eval_results_dir: str = ".eval-results"

    # embeddings
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dims: int = 384
    fastembed_cache: str = ".fastembed_cache"

    # timeouts / budgets
    model_timeout_s: int = 90
    mcp_timeout_s: int = 30
    exec_max_timeout_s: int = 60
    turn_timeout_s: int = 240

    # config directory (agents.yaml, models.yaml, prompts.yaml, …)
    config_dir: Path = Field(default=REPO_ROOT / "config")
    prompts_dir: Path = Field(default=REPO_ROOT / "prompts")

    @property
    def tracing_enabled(self) -> bool:
        """Whether traces are sent: tracing is on and a LangSmith key is set."""
        return self.langsmith_tracing and bool(self.langsmith_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide `Settings`, built once from the environment and cached.

    Returns:
        The cached `Settings` instance.
    """
    return Settings()
