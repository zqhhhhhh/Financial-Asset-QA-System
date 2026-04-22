from collections import defaultdict


class SessionService:
    """In-memory conversation history per session."""

    def __init__(self) -> None:
        # session_id -> list of {"role": "user"|"assistant", "content": str}
        self._sessions: dict[str, list[dict]] = defaultdict(list)

    def get_history(self, session_id: str) -> list[dict]:
        return list(self._sessions[session_id])

    def append_message(self, session_id: str, role: str, content: str) -> None:
        self._sessions[session_id].append({"role": role, "content": content})

    def reset(self, session_id: str) -> None:
        self._sessions[session_id] = []
