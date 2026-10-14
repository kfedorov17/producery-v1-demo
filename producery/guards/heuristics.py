"""Deterministic pre-classification Returns None to defer to the model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Heuristic:
    """One pattern rule: the class it assigns, its confidence, the compiled pattern and the reason."""

    cls: str
    confidence: float
    pattern: re.Pattern[str]
    why: str


class HeuristicClassifier:
    """Rule-based classifier run before the model: known contacts, patterns, network and carrier domains."""

    def __init__(self, carriers: list[dict], networks: list[dict], rules: list[Heuristic]) -> None:
        """Hold the classification tables.

        Args:
            carriers: Carrier entries with `name` and `domains`.
            networks: Network entries with `name` and `domains`.
            rules: Pattern rules, checked in order.
        """
        self.carriers = carriers
        self.networks = networks
        self.rules = rules

    @classmethod
    def load(cls, path: Path) -> HeuristicClassifier:
        """Load carriers, networks and rules from a YAML file; every section may be absent.

        Args:
            path: Path to the YAML file.

        Returns:
            A classifier with the compiled (case-insensitive) rules.
        """
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rules = [
            Heuristic(r["class"], float(r["confidence"]), re.compile(r["pattern"], re.I), r["why"])
            for r in raw.get("rules", [])
        ]
        return cls(raw.get("carriers", []), raw.get("networks", []), rules)

    @staticmethod
    def _domain(email: str) -> str:
        return email.split("@")[-1].lower() if "@" in email else ""

    def classify(self, email: dict[str, Any], known_contacts: list[str]) -> dict[str, Any] | None:
        """Classify an email deterministically, or return None to defer to the model.

        Checks in order: a known contact sender (`client_service`, 0.9), the pattern rules against sender,
        subject and the first 3000 body characters, a network domain (`network_thread`, 0.9), then a carrier
        domain (`carrier_thread`, 0.9). The first hit wins.

        Args:
            email: The email mapping with `from_email`, `subject` and `body`.
            known_contacts: Client contact addresses, compared case-insensitively.

        Returns:
            `{"classification", "confidence", "reason", "source": "heuristic"}`, or None when nothing matched.
        """
        from_email = (email.get("from_email") or "").lower()
        haystack = f"{from_email}\n{email.get('subject') or ''}\n{(email.get('body') or '')[:3000]}"
        dom = self._domain(from_email)
        if from_email in {c.lower() for c in known_contacts}:
            return {
                "classification": "client_service",
                "confidence": 0.9,
                "reason": "sender is a known client contact",
                "source": "heuristic",
            }
        for rule in self.rules:
            if rule.pattern.search(haystack):
                return {
                    "classification": rule.cls,
                    "confidence": rule.confidence,
                    "reason": rule.why,
                    "source": "heuristic",
                }
        for n in self.networks:
            if dom in (n.get("domains") or []):
                return {
                    "classification": "network_thread",
                    "confidence": 0.9,
                    "reason": f"sender domain matches network {n['name']}",
                    "source": "heuristic",
                }
        for c in self.carriers:
            if dom in (c.get("domains") or []):
                return {
                    "classification": "carrier_thread",
                    "confidence": 0.9,
                    "reason": f"sender domain matches carrier {c['name']}",
                    "source": "heuristic",
                }
        return None
