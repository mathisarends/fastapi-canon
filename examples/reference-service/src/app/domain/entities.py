from datetime import UTC, datetime
from uuid import UUID, uuid4


class Entity:
    def __init__(
        self,
        id: UUID | None = None,
        created_time: datetime | None = None,
    ) -> None:
        self._id = id if id is not None else uuid4()
        self._created_at = (
            created_time if created_time is not None else datetime.now(UTC)
        )

    @property
    def id(self) -> UUID:
        return self._id

    @property
    def created_at(self) -> datetime:
        return self._created_at


# Semantic sugar: aggregates are entities that form a consistency boundary.
Aggregate = Entity
