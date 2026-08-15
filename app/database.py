"""SQLite persistence for conversations and messages."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import APP_DIR


DB_PATH = APP_DIR / "chats.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Conversation:
    id: str
    title: str
    provider: str
    model: str
    effort: str
    created_at: str
    updated_at: str


@dataclass
class Message:
    id: str
    conversation_id: str
    role: str
    content: str
    attachments_json: str
    created_at: str

    @property
    def attachments(self) -> list[dict[str, Any]]:
        if not self.attachments_json:
            return []
        try:
            data = json.loads(self.attachments_json)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []


class Database:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    effort TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, created_at);
                """
            )

    def list_conversations(self) -> list[Conversation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [Conversation(**dict(row)) for row in rows]

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return Conversation(**dict(row)) if row else None

    def create_conversation(
        self,
        title: str = "New chat",
        provider: str = "xAI",
        model: str = "",
        effort: str = "medium",
    ) -> Conversation:
        conv = Conversation(
            id=str(uuid.uuid4()),
            title=title,
            provider=provider,
            model=model,
            effort=effort,
            created_at=_now(),
            updated_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, title, provider, model, effort, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conv.id,
                    conv.title,
                    conv.provider,
                    conv.model,
                    conv.effort,
                    conv.created_at,
                    conv.updated_at,
                ),
            )
        return conv

    def update_conversation(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        conv = self.get_conversation(conversation_id)
        if not conv:
            return
        if title is not None:
            conv.title = title
        if provider is not None:
            conv.provider = provider
        if model is not None:
            conv.model = model
        if effort is not None:
            conv.effort = effort
        conv.updated_at = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET title = ?, provider = ?, model = ?, effort = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    conv.title,
                    conv.provider,
                    conv.model,
                    conv.effort,
                    conv.updated_at,
                    conversation_id,
                ),
            )

    def delete_conversation(self, conversation_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def list_messages(self, conversation_id: str) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
                (conversation_id,),
            ).fetchall()
        return [Message(**dict(row)) for row in rows]

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> Message:
        msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role=role,
            content=content,
            attachments_json=json.dumps(attachments or []),
            created_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (id, conversation_id, role, content, attachments_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.id,
                    msg.conversation_id,
                    msg.role,
                    msg.content,
                    msg.attachments_json,
                    msg.created_at,
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (_now(), conversation_id),
            )
        return msg

    def update_message_content(self, message_id: str, content: str, attachments: list[dict[str, Any]] | None = None) -> None:
        with self._connect() as conn:
            if attachments is not None:
                conn.execute(
                    "UPDATE messages SET content = ?, attachments_json = ? WHERE id = ?",
                    (content, json.dumps(attachments), message_id),
                )
            else:
                conn.execute(
                    "UPDATE messages SET content = ? WHERE id = ?",
                    (content, message_id),
                )
