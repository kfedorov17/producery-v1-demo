"""Async Postgres access: one injected pool per process. Least privilege is applied per statement with SET LOCAL ROLE."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


class Database:
    """Async psycopg connection-pool wrapper with lazy opening and a role-restricted read helper."""

    def __init__(
        self, dsn: str, *, min_size: int = 1, max_size: int = 8, statement_timeout_ms: int = 15_000
    ) -> None:
        """Create the pool without opening it.

        Args:
            dsn: Postgres connection string.
            min_size: Minimum pooled connections.
            max_size: Maximum pooled connections.
            statement_timeout_ms: ``statement_timeout`` applied inside ``fetch_as_role`` transactions.
        """
        self._dsn = dsn
        self._pool = AsyncConnectionPool(
            dsn, min_size=min_size, max_size=max_size, open=False, kwargs={"row_factory": dict_row}
        )
        self._stmt_timeout = statement_timeout_ms
        self._opened = False

    async def open(self) -> None:
        """Idempotent; also called lazily so the pool binds to the event loop that actually runs it."""
        if not self._opened:
            await self._pool.open()
            self._opened = True

    async def close(self) -> None:
        """Close the pool."""
        await self._pool.close()

    async def ping(self) -> bool:
        """Return True when ``select 1`` succeeds on a pooled connection, False on any exception."""
        try:
            await self.open()
            async with self._pool.connection() as conn:
                await conn.execute("select 1")
            return True
        except Exception:
            return False

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection[Any]]:
        """Yield a pooled connection, opening the pool first when needed."""
        await self.open()
        async with self._pool.connection() as conn:
            yield conn

    async def fetch(self, sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
        """Run a statement and return its rows as dicts (empty when it produces no result set).

        Args:
            sql: The SQL statement.
            params: Positional or named parameters.
        """
        await self.open()
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            if cur.description is None:
                return []
            return [dict(r) for r in await cur.fetchall()]

    async def fetchone(self, sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
        """Return the first row of ``fetch``, or None when there is none.

        Args:
            sql: The SQL statement.
            params: Positional or named parameters.
        """
        rows = await self.fetch(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql: str, params: tuple | dict | None = None) -> None:
        """Run a statement on a pooled connection and discard any result.

        Args:
            sql: The SQL statement.
            params: Positional or named parameters.
        """
        await self.open()
        async with self._pool.connection() as conn:
            await conn.execute(sql, params)

    async def fetch_as_role(
        self, sql: str, role: str, *, row_cap: int, params: tuple | dict | None = None
    ) -> tuple[list[dict[str, Any]], bool]:
        """Run a read-only query under a restricted role inside one transaction (SET LOCAL scope).

        Args:
            sql: The SQL statement.
            role: Postgres role to ``SET LOCAL ROLE`` to.
            row_cap: Maximum rows returned; one extra row is fetched to detect truncation.
            params: Positional or named parameters.

        Returns:
            The rows (at most ``row_cap``) and whether more rows were available.
        """
        await self.open()
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(f"set local statement_timeout = {int(self._stmt_timeout)}")
                await conn.execute("set local transaction read only")
                await conn.execute(f'set local role "{role}"')
                cur = await conn.execute(sql, params)
                rows = [dict(r) for r in await cur.fetchmany(row_cap + 1)] if cur.description else []
        return rows[:row_cap], len(rows) > row_cap

    async def apply_sql(self, sql: str) -> None:
        """Run raw SQL text with no parameters and no result.

        Args:
            sql: The SQL text.
        """
        await self.open()
        async with self._pool.connection() as conn:
            await conn.execute(sql)


def jsonb(value: Any) -> str:
    """Serialize a value to a JSON string for a ``::jsonb`` parameter (non-serializable values via ``str``).

    Args:
        value: The value to serialize.
    """
    return json.dumps(value, default=str, ensure_ascii=False)
