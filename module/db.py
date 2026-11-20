"""SQLite persistence for bot download tasks.

A tiny wrapper around sqlite3 (stdlib only, no extra dependency).
All functions are synchronous and cheap enough to be called directly
from the bot's asyncio event loop (single writer, short transactions).
"""

import os
import sqlite3
from datetime import datetime
from typing import List, Optional

_conn: Optional[sqlite3.Connection] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_download_task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    start_id INTEGER NOT NULL DEFAULT 0,
    end_id INTEGER NOT NULL DEFAULT 0,
    custom_path TEXT,
    download_filter TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL)
"""

_DOWNLOADED_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloaded_message (
    chat_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    file_name TEXT,
    downloaded_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
)
"""


def init_db(db_path: str) -> None:
    """Open (or create) the task database."""
    global _conn
    if _conn is not None:
        return
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute(_SCHEMA)
    _conn.execute(_DOWNLOADED_SCHEMA)
    _conn.commit()


def mark_downloaded(
    chat_id, message_id: Optional[int], file_name: Optional[str] = None
) -> None:
    """Record a successfully downloaded message (persists across restarts)."""
    if _conn is None or message_id is None:
        return
    _conn.execute(
        "INSERT OR REPLACE INTO downloaded_message "
        "(chat_id, message_id, file_name, downloaded_at) VALUES (?, ?, ?, ?)",
        (
            str(chat_id),
            message_id,
            file_name,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    _conn.commit()


def has_downloaded(chat_id, message_id: Optional[int]) -> bool:
    """Whether this message was already downloaded (recorded in db)."""
    if _conn is None or message_id is None:
        return False
    cur = _conn.execute(
        "SELECT 1 FROM downloaded_message "
        "WHERE chat_id = ? AND message_id = ? LIMIT 1",
        (str(chat_id), message_id),
    )
    return cur.fetchone() is not None


def add_task(
    chat_id,
    start_id: int,
    end_id: int,
    custom_path: Optional[str],
    download_filter: Optional[str],
) -> Optional[int]:
    """Insert a new running task, return its row id."""
    if _conn is None:
        return None
    now = datetime.now().isoformat(timespec="seconds")
    cur = _conn.execute(
        "INSERT INTO bot_download_task "
        "(chat_id, start_id, end_id, custom_path, download_filter, "
        " status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
        (
            str(chat_id),
            start_id,
            end_id,
            custom_path,
            download_filter,
            now,
            now,
        ),
    )
    _conn.commit()
    return cur.lastrowid


def update_task_status(task_id: int, status: str) -> None:
    """Update the status of a task ('running' / 'done' / 'stopped')."""
    if _conn is None or task_id is None:
        return
    _conn.execute(
        "UPDATE bot_download_task SET status = ?, updated_at = ? WHERE id = ?",
        (status, datetime.now().isoformat(timespec="seconds"), task_id),
    )
    _conn.commit()


def delete_task(task_id: int) -> bool:
    """Delete a task row. Returns True if a row was removed."""
    if _conn is None or task_id is None:
        return False
    cur = _conn.execute(
        "DELETE FROM bot_download_task WHERE id = ?", (task_id,)
    )
    _conn.commit()
    return cur.rowcount > 0


def get_task(task_id: int) -> Optional[sqlite3.Row]:
    """Fetch a single task row by id."""
    if _conn is None:
        return None
    cur = _conn.execute(
        "SELECT * FROM bot_download_task WHERE id = ?", (task_id,)
    )
    return cur.fetchone()


def get_unfinished_tasks() -> List[sqlite3.Row]:
    """All tasks that are not finished, oldest first."""
    if _conn is None:
        return []
    cur = _conn.execute(
        "SELECT * FROM bot_download_task WHERE status != 'done' ORDER BY id"
    )
    return cur.fetchall()


def get_recent_tasks(limit: int = 30) -> List[sqlite3.Row]:
    """The most recent tasks regardless of status, newest first."""
    if _conn is None:
        return []
    cur = _conn.execute(
        "SELECT * FROM bot_download_task ORDER BY id DESC LIMIT ?", (limit,)
    )
    return cur.fetchall()
