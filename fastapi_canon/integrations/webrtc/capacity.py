"""Session slots shared across WebRTC declarations."""


class SessionCapacity:
    """Limit concurrent sessions across every declaration that shares this instance.

    Pass one instance as ``capacity=`` to several ``WebRtcRouter.session()``
    declarations, on any routers or features. A session holds one slot from
    admission until its cleanup finished; a full capacity refuses new sessions
    with ``session_limit`` before a peer is created. The instance is process
    state: every application that declares it shares its slots.
    """

    __slots__ = ("_active", "max_sessions")

    def __init__(self, *, max_sessions: int) -> None:
        raw: object = max_sessions
        if type(raw) is not int or max_sessions <= 0:
            raise ValueError("max_sessions must be positive")
        self.max_sessions = max_sessions
        self._active = 0

    def __repr__(self) -> str:
        return (
            f"SessionCapacity(max_sessions={self.max_sessions}, active={self._active})"
        )

    @property
    def active(self) -> int:
        """Number of slots held by open sessions."""
        return self._active

    @property
    def full(self) -> bool:
        return self._active >= self.max_sessions


class SlotReservation:
    """One session's slots in every capacity that applies to it."""

    __slots__ = ("_capacities", "_held")

    def __init__(self, *capacities: SessionCapacity) -> None:
        self._capacities = capacities
        self._held = True
        for capacity in capacities:
            capacity._active += 1

    def release(self) -> None:
        if self._held:
            self._held = False
            for capacity in self._capacities:
                capacity._active -= 1
