"""Deterministic SQL guard: one SELECT, allowlisted views only, denylisted keywords."""

from __future__ import annotations

import re

ALLOWLISTED_VIEWS: tuple[str, ...] = ("v_households", "v_policies", "v_renewals", "v_appointments")
_DENY = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|copy|truncate|set|reset|vacuum|analyze|pg_sleep|pg_read_file|lo_import|dblink)\b",
    re.I,
)
_IDENT = r'(?:"[^"]+"|[a-zA-Z_]\w*)(?:\s*\.\s*(?:"[^"]+"|[a-zA-Z_]\w*))*'
# every relation that follows FROM or JOIN, through optional parentheses, and every comma-separated relation
# after it (`from a, b`); aliases (`as x`, `x`) are skipped before looking for the next comma
_RELATION = re.compile(rf"\b(?:from|join)\s*\(*\s*({_IDENT})", re.I)
_COMMA_RELATION = re.compile(
    rf"\b(?:from|join)\s*\(*\s*{_IDENT}(?:\s+(?:as\s+)?[a-zA-Z_]\w*)?((?:\s*,\s*{_IDENT}(?:\s+(?:as\s+)?[a-zA-Z_]\w*)?)+)",
    re.I,
)
_CTE = re.compile(r"\b(\w+)\s+as\s*\(", re.I)


def _relations(q: str) -> list[str]:
    """Return every relation name a query reads, as written (quotes and schema prefix kept)."""
    names = _RELATION.findall(q)
    for tail in _COMMA_RELATION.findall(q):
        names += [m.strip() for m in re.findall(rf",\s*({_IDENT})", tail)]
    return names


class SqlGuardError(ValueError):
    """Raised with a message that teaches the caller how to fix the query."""


def guard_sql(query: str) -> str:
    """Validate a read-only query and return it stripped of surrounding whitespace and a trailing semicolon.

    The checks are purely lexical (regular expressions): one statement, a leading SELECT or WITH, no
    denylisted keyword anywhere, no quoted identifier (the views need none), and every relation named after
    FROM or JOIN, including comma-separated and parenthesised ones, either an allowlisted view or a CTE name
    defined in the query. The read-only role is the second line: it refuses the application tables.

    Args:
        query: The SQL text to check.

    Returns:
        The cleaned query, ready to be executed under the read-only role.

    Raises:
        SqlGuardError: When any check fails; the message tells the caller how to fix the query.
    """
    q = (query or "").strip().rstrip(";").strip()
    if not q:
        raise SqlGuardError("empty query; send one SELECT over the allowlisted views")
    if ";" in q:
        raise SqlGuardError("exactly one statement allowed; remove the ';' and any second statement")
    if not re.match(r"^(select|with)\b", q, re.I):
        raise SqlGuardError("only SELECT (or WITH … SELECT) queries are allowed")
    hit = _DENY.search(q)
    if hit:
        raise SqlGuardError(f"disallowed keyword: {hit.group(1).lower()}; the tool is read-only")
    if '"' in q:
        raise SqlGuardError("quoted identifiers are not allowed; the allowlisted views need no quoting")
    ctes = {m.group(1).lower() for m in _CTE.finditer(q)}
    for rel in _relations(q):
        name = re.sub(r"\s+", "", rel).lower().split(".")[-1]
        if name not in ALLOWLISTED_VIEWS and name not in ctes:
            raise SqlGuardError(
                f'relation "{rel}" is not an allowlisted view: {", ".join(ALLOWLISTED_VIEWS)}'
            )
    return q
