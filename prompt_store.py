import asyncio
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class PromptRecord:
    created_at: int
    platform: str
    session_id: str
    sender_id: str
    sender_name: str
    sender_avatar: str
    prompt: str
    input_outline: str
    output_path: str
    reference_paths: str
    status: str
    error: str


class PromptStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def add_record(self, record: PromptRecord) -> None:
        await asyncio.to_thread(self._add_record_sync, record)

    async def list_records(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_records_sync)

    async def get_record(self, record_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_record_sync, record_id)

    async def get_stats(self) -> dict[str, int]:
        return await asyncio.to_thread(self._get_stats_sync)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_sync(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    sender_avatar TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL,
                    input_outline TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    reference_paths TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    error TEXT NOT NULL
                )
                """,
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(prompt_records)").fetchall()}
            if "reference_paths" not in columns:
                conn.execute("ALTER TABLE prompt_records ADD COLUMN reference_paths TEXT NOT NULL DEFAULT ''")
            if "sender_avatar" not in columns:
                conn.execute("ALTER TABLE prompt_records ADD COLUMN sender_avatar TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_records_created_at ON prompt_records(created_at DESC)")

    def _add_record_sync(self, record: PromptRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO prompt_records (
                    created_at, platform, session_id, sender_id, sender_name,
                    sender_avatar, prompt, input_outline, output_path, reference_paths, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.created_at or int(time.time()),
                    record.platform,
                    record.session_id,
                    record.sender_id,
                    record.sender_name,
                    record.sender_avatar,
                    record.prompt,
                    record.input_outline,
                    record.output_path,
                    record.reference_paths,
                    record.status,
                    record.error,
                ),
            )

    def _list_records_sync(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM prompt_records WHERE status = 'success' ORDER BY created_at DESC, id DESC",
            ).fetchall()
        return [dict(row) for row in rows]

    def _get_record_sync(self, record_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM prompt_records WHERE id = ? AND status = 'success'",
                (record_id,),
            ).fetchone()
        return dict(row) if row else None

    def _get_stats_sync(self) -> dict[str, int]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM prompt_records WHERE status = 'success'").fetchone()[0]
        return {"total": total}
