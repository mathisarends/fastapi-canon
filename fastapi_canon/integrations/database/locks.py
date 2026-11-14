"""Explicit PostgreSQL advisory transaction locks on the scoped session."""

from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AcquireTransactionLock:
    """Coordinate transactions across processes until commit or rollback.

    SQLite does not implement PostgreSQL advisory locks. Its default is to fail
    explicitly; sqlite='ignore' is an opt-in for callers whose SQLite execution
    does not require these concurrency guarantees, such as isolated SQL tests.
    """

    def __init__(
        self, session: AsyncSession, *, sqlite: Literal["error", "ignore"] = "error"
    ) -> None:
        if sqlite not in {"error", "ignore"}:
            raise ValueError("sqlite lock policy must be 'error' or 'ignore'")
        self._session = session
        self._sqlite = sqlite

    async def __call__(self, key: int) -> None:
        if type(key) is not int or not -(2**63) <= key < 2**63:
            raise ValueError("Advisory lock key must be a signed 64-bit integer")
        dialect = self._session.get_bind().dialect.name
        if dialect == "sqlite" and self._sqlite == "ignore":
            return
        if dialect != "postgresql":
            raise NotImplementedError(
                "Advisory transaction locks require PostgreSQL; "
                "SQLite offers no equivalent guarantee"
            )
        # Use the session's current connection so the lock shares its exact
        # transaction, including callers that use savepoints or explicit commits.
        connection = await self._session.connection()
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
        )

    async def acquire(self, key: int) -> None:
        """Acquire a lock through the supported named operation."""
        await self(key)


TransactionLocks = AcquireTransactionLock
