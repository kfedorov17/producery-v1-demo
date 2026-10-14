"""Deterministic compliance gate — code, not model. Rules come from config/compliance_rules.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Rule:
    """One compliance rule: a code, the compiled case-insensitive pattern and the reason it flags."""

    code: str
    pattern: re.Pattern[str]
    why: str


class ComplianceGate:
    """Regex-based gate that flags text matching any loaded compliance rule."""

    def __init__(self, rules: list[Rule]) -> None:
        """Hold the rules to check against.

        Args:
            rules: The rules, checked in order.
        """
        self.rules = rules

    @classmethod
    def load(cls, path: Path) -> ComplianceGate:
        """Load rules from a YAML file with a `rules` list of `code`, `pattern` and `why` entries.

        Args:
            path: Path to the YAML file (normally config/compliance_rules.yaml).

        Returns:
            A gate holding the compiled rules.

        Raises:
            ValueError: If the file defines no rules.
        """
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rules = [Rule(r["code"], re.compile(r["pattern"], re.I), r["why"]) for r in raw.get("rules", [])]
        if not rules:
            raise ValueError(f"{path}: no rules")
        return cls(rules)

    def check(self, text: str) -> list[dict[str, Any]]:
        """Return one flag per rule whose pattern matches the text.

        Args:
            text: The text to check; None is treated as empty.

        Returns:
            Flags of the form `{"code", "why", "excerpt"}`, excerpt being the first match cut to 60 chars.
        """
        flags = []
        for rule in self.rules:
            m = rule.pattern.search(text or "")
            if m:
                flags.append({"code": rule.code, "why": rule.why, "excerpt": m.group(0)[:60]})
        return flags
