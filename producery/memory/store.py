"""Long-term memory on the LangGraph Store: three kinds with explicit write policies.

  ("memory", "semantic",   agency, thread_class)  facts about the world — written from EXECUTED record updates only,
                                                  carrying the extractor's per-field confidence and the proposal id
  ("memory", "episodic",   agency, thread_class)  what happened per thread — written on every terminal outcome
  ("memory", "procedural", agency, thread_class)  how the owner wants things done — ONLY from human edits / reasoned rejections

The thread class (crew | eval | smoke) is part of the namespace so evaluation runs never write into, or recall
from, production memory. Semantic search is native to the store (pgvector index on "text"); short-term memory is
graph state in the checkpointer. The store itself is the one LangGraph injects at runtime (`get_store()`), so the
same code runs under the FastAPI server and under LangSmith Deployment.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from langgraph.store.base import BaseStore

from producery.timeutil import utc_now_iso

logger = logging.getLogger(__name__)
KINDS = ("semantic", "episodic", "procedural")


class MemoryStore:
    """Write-policy and recall facade over one agency's memory namespaces in the LangGraph store."""

    def __init__(
        self,
        store: BaseStore,
        agency_id: str,
        thread_class: str = "crew",
        ttl_days: dict[str, int | None] | None = None,
    ) -> None:
        """Bind the facade to a store, an agency and a thread class.

        Args:
            store: The LangGraph store the records are written to and searched in.
            agency_id: Agency segment of every namespace.
            thread_class: Thread class segment of every namespace (crew, eval or smoke).
            ttl_days: Optional TTL in days per kind; kinds that are absent or falsy get no TTL.
        """
        self.store = store
        self.agency_id = agency_id
        self.thread_class = thread_class
        self.ttl_days = ttl_days or {}

    def ns(self, kind: str) -> tuple[str, str, str, str]:
        """Return the store namespace for a memory kind.

        Args:
            kind: One of ``KINDS`` (asserted).

        Returns:
            The tuple ("memory", kind, agency_id, thread_class).
        """
        assert kind in KINDS
        return ("memory", kind, self.agency_id, self.thread_class)

    def _ttl_minutes(self, kind: str) -> float | None:
        days = self.ttl_days.get(kind)
        return float(days * 24 * 60) if days else None

    async def _put(self, kind: str, key: str, value: dict[str, Any]) -> str:
        value = {**value, "kind": kind, "written_at": utc_now_iso()}
        ttl = self._ttl_minutes(kind)
        if ttl is None:
            await self.store.aput(self.ns(kind), key, value, index=["text"])
        else:
            await self.store.aput(self.ns(kind), key, value, index=["text"], ttl=ttl)
        logger.info("memory_written kind=%s class=%s key=%s", kind, self.thread_class, key)
        return key

    # ---- write policies ------------------------------------------------------
    async def write_semantic(self, text: str, *, source: str, confidence: float) -> str:
        """Write a fact; the key is derived from the text, so the same fact is stored once.

        Args:
            text: The fact, also the indexed search text.
            source: Where the fact came from, as given by the caller.
            confidence: Extractor confidence, stored rounded to three decimals.

        Returns:
            The record key.
        """
        key = "fact-" + hashlib.sha1(text.encode()).hexdigest()[:12]  # noqa: S324 — dedupe key, not security
        return await self._put(
            "semantic", key, {"text": text, "source": source, "confidence": round(float(confidence), 3)}
        )

    async def write_episodic(
        self,
        thread_id: str,
        *,
        sender: str | None,
        classification: str,
        outcome: str,
        decision: str | None,
        trace_id: str | None,
    ) -> str:
        """Write the outcome of one thread under the key ``ep-<thread_id>``, replacing any earlier record.

        Args:
            thread_id: Thread the outcome belongs to.
            sender: Sender e-mail, or None when withheld.
            classification: The thread's classification.
            outcome: Terminal outcome of the thread.
            decision: The human decision, if any.
            trace_id: LangSmith trace id of the turn, if known.

        Returns:
            The record key.
        """
        who = sender or "(withheld)"
        text = f"{who}: {classification} → {outcome}" + (f" (decision: {decision})" if decision else "")
        return await self._put(
            "episodic",
            f"ep-{thread_id}",
            {
                "text": text,
                "thread_id": thread_id,
                "sender": sender,
                "classification": classification,
                "outcome": outcome,
                "decision": decision,
                "trace_id": trace_id,
            },
        )

    async def write_procedural(self, text: str, *, origin: str, source: str) -> str:
        """Write an owner preference; only human edits and reasoned rejections may originate one.

        Args:
            text: The preference, also the indexed search text.
            origin: ``human_edit`` or ``human_reject`` (asserted).
            source: Where the preference came from, as given by the caller.

        Returns:
            The record key, derived from the text.
        """
        assert origin in ("human_edit", "human_reject")
        key = "pref-" + hashlib.sha1(text.encode()).hexdigest()[:12]  # noqa: S324
        return await self._put("procedural", key, {"text": text, "origin": origin, "source": source})

    # ---- recall ----------------------------------------------------------------
    async def recall(self, *, query: str, sender: str, limit: int = 5) -> dict[str, list[dict[str, Any]]]:
        """Search the three kinds for a query and a sender.

        Semantic and procedural memory are searched by ``query``; episodic memory is searched by ``sender``
        and filtered to that sender (at most three records), or skipped when ``sender`` is empty.

        Args:
            query: Natural-language search text.
            sender: Sender e-mail for the episodic lookup; an empty string disables it.
            limit: Maximum semantic and procedural hits each.

        Returns:
            A dict keyed semantic/procedural/episodic; each hit carries its key, score and stored fields.
        """
        out: dict[str, list[dict[str, Any]]] = {}
        semantic = await self.store.asearch(self.ns("semantic"), query=query, limit=limit)
        procedural = await self.store.asearch(self.ns("procedural"), query=query, limit=limit)
        episodic = (
            await self.store.asearch(self.ns("episodic"), query=sender, filter={"sender": sender}, limit=3)
            if sender
            else []
        )
        for name, items in (("semantic", semantic), ("procedural", procedural), ("episodic", episodic)):
            out[name] = [{"key": it.key, "score": round(it.score or 0.0, 4), **it.value} for it in items]
        return out

    async def count(self, kind: str) -> int:
        """Return the number of records of a kind, capped at 1000 by the underlying search.

        Args:
            kind: One of ``KINDS``.
        """
        items = await self.store.asearch(self.ns(kind), limit=1000)
        return len(items)
