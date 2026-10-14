"""Trace masking.

Client text reaches LangSmith in run inputs and outputs. The application's own stores have TTLs and withholding
rules; the traces had none. This module builds the LangSmith client every trace goes through with an anonymizer:
e-mail addresses keep their domain (sender-domain routing stays debuggable) and lose the local part; phone numbers
and US SSN shapes are redacted; the sender's display name is masked by field. Bodies are kept — the draft and the
brief are what an operator reads a trace for — but the identifying fields in them are gone.

The same `mask_text` is what `scripts/mint_golden.py` applies when it copies a trace into the repository.
"""

from __future__ import annotations

import re
from typing import Any

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)"  # North American shapes
    r"|\+\d{1,3}[ .-]?\d[\d .-]{7,12}\d(?!\d)"  # E.164 with any grouping
)
_SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_NAME_FIELDS = frozenset({"from_name", "display_name", "decided_by"})


def mask_text(text: str) -> str:
    """Redact identifiers inside free text. Domains survive; identities do not."""
    text = _SSN_RE.sub("[ssn]", text)
    text = _PHONE_RE.sub("[phone]", text)
    return _EMAIL_RE.sub(lambda m: f"***@{m.group(1)}", text)


def _replacer(value: str, path: list[str | int]) -> str:
    if path and isinstance(path[-1], str) and path[-1] in _NAME_FIELDS and value:
        return "[name]"
    return mask_text(value)


def build_client(api_key: str, api_url: str, *, mask: bool = True) -> Any:
    """The LangSmith client traces are shipped through; with `mask` every string node is anonymized client-side."""
    from langsmith import Client

    if not mask:
        return Client(api_key=api_key, api_url=api_url)
    from langsmith.anonymizer import create_anonymizer

    return Client(api_key=api_key, api_url=api_url, anonymizer=create_anonymizer(_replacer))


def build_tracer(project: str, client: Any) -> Any:
    """Return a LangChainTracer bound to the masked client.

    Passed in every run config so LangChain does not create its own (unmasked) tracer from the environment.

    Args:
        project: LangSmith project name.
        client: The client from ``build_client``.
    """
    from langchain_core.tracers.langchain import LangChainTracer

    return LangChainTracer(project_name=project, client=client)
