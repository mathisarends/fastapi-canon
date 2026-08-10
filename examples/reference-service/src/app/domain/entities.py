from datetime import UTC, datetime
from uuid import UUID, uuid4


class Entity:
    def __init__(
        self,
        id: UUID | None = None,
        created_time: datetime | None = None,
    ) -> None:
        self.id = id or uuid4()
        self.created_at = created_time or datetime.now(UTC)


# Semantic sugar: aggregates are entities that form a consistency boundary.
Aggregate = Entity
