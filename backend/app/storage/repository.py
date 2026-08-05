import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from backend.app.domain.models import Conversation, Message, Mission, MissionEvent


class Repository:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                """
            )

    def create_mission(self, mission: Mission) -> None:
        with self._transaction() as db:
            db.execute(
                "INSERT INTO missions (id, data) VALUES (?, ?)",
                (mission.id, mission.model_dump_json()),
            )
            self.add_event(mission.id, "mission_created", mission.model_dump(mode="json"), db=db)

    def update_mission(self, mission: Mission) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE missions SET data = ? WHERE id = ?",
                (mission.model_dump_json(), mission.id),
            )
            self.add_event(mission.id, "mission_updated", mission.model_dump(mode="json"), db=db)

    def get_mission(self, mission_id: str) -> Optional[Mission]:
        with self._transaction() as db:
            row = db.execute("SELECT data FROM missions WHERE id = ?", (mission_id,)).fetchone()
        return Mission.model_validate_json(row["data"]) if row else None

    def list_missions(self) -> list[Mission]:
        with self._transaction() as db:
            rows = db.execute("SELECT data FROM missions ORDER BY id DESC").fetchall()
        return [Mission.model_validate_json(row["data"]) for row in rows]

    def add_message(self, message: Message) -> None:
        with self._transaction() as db:
            db.execute(
                "INSERT INTO messages (id, mission_id, data) VALUES (?, ?, ?)",
                (message.id, message.mission_id, message.model_dump_json()),
            )
            self.add_event(message.mission_id, "message", message.model_dump(mode="json"), db=db)

    def list_messages(self, mission_id: str) -> list[Message]:
        with self._transaction() as db:
            rows = db.execute(
                "SELECT data FROM messages WHERE mission_id = ?",
                (mission_id,),
            ).fetchall()
        messages = [Message.model_validate_json(row["data"]) for row in rows]
        return sorted(messages, key=lambda message: (message.created_at, message.id))

    def add_event(
        self,
        mission_id: str,
        event_type: str,
        payload: dict[str, Any],
        db: Optional[sqlite3.Connection] = None,
    ) -> str:
        owns_connection = db is None
        if owns_connection:
            with self._transaction() as connection:
                return self.add_event(mission_id, event_type, payload, db=connection)

        cursor = db.execute(
            "INSERT INTO events (mission_id, event_type, payload, created_at) VALUES (?, ?, ?, datetime('now'))",
            (mission_id, event_type, json.dumps(payload, ensure_ascii=False)),
        )
        return str(cursor.lastrowid)

    def list_events_after(self, mission_id: str, after_id: int) -> list[MissionEvent]:
        with self._transaction() as db:
            rows = db.execute(
                """
                SELECT id, mission_id, event_type, payload, created_at
                FROM events
                WHERE mission_id = ? AND id > ?
                ORDER BY id ASC
                """,
                (mission_id, after_id),
            ).fetchall()
        events: list[MissionEvent] = []
        for row in rows:
            events.append(
                MissionEvent(
                    id=str(row["id"]),
                    mission_id=row["mission_id"],
                    event_type=row["event_type"],
                    payload=json.loads(row["payload"]),
                    created_at=row["created_at"],
                )
            )
        return events

    def list_events_for_turn(self, mission_id: str, turn_id: str) -> list[MissionEvent]:
        with self._transaction() as db:
            rows = db.execute(
                """
                SELECT id, mission_id, event_type, payload, created_at
                FROM events
                WHERE mission_id = ?
                ORDER BY id ASC
                """,
                (mission_id,),
            ).fetchall()
        events: list[MissionEvent] = []
        for row in rows:
            payload = json.loads(row["payload"])
            if payload.get("turn_id") != turn_id:
                continue
            events.append(
                MissionEvent(
                    id=str(row["id"]),
                    mission_id=row["mission_id"],
                    event_type=row["event_type"],
                    payload=payload,
                    created_at=row["created_at"],
                )
            )
        return events

    def create_conversation(self, conversation: Conversation) -> None:
        with self._transaction() as db:
            db.execute(
                "INSERT INTO conversations (id, data) VALUES (?, ?)",
                (conversation.id, conversation.model_dump_json()),
            )

    def update_conversation(self, conversation: Conversation) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE conversations SET data = ? WHERE id = ?",
                (conversation.model_dump_json(), conversation.id),
            )

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        with self._transaction() as db:
            row = db.execute(
                "SELECT data FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return Conversation.model_validate_json(row["data"]) if row else None

    def list_conversations(self) -> list[Conversation]:
        with self._transaction() as db:
            rows = db.execute("SELECT data FROM conversations").fetchall()
        conversations = [Conversation.model_validate_json(row["data"]) for row in rows]
        conversations.sort(key=lambda c: c.updated_at, reverse=True)
        return conversations

    def list_missions_for_conversation(self, conversation_id: str) -> list[Mission]:
        with self._transaction() as db:
            rows = db.execute("SELECT data FROM missions").fetchall()
        missions = [Mission.model_validate_json(row["data"]) for row in rows]
        return sorted(
            [m for m in missions if m.conversation_id == conversation_id],
            key=lambda m: m.created_at,
        )

    def list_messages_for_conversation(self, conversation_id: str) -> list[Message]:
        with self._transaction() as db:
            rows = db.execute("SELECT data FROM messages").fetchall()
        messages = [Message.model_validate_json(row["data"]) for row in rows]
        return sorted(
            [m for m in messages if m.conversation_id == conversation_id],
            key=lambda m: (m.created_at, m.id),
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            with connection:
                yield connection

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection
