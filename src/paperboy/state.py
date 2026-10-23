"""Remembers which posts were processed, so runs are idempotent."""

import fcntl
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS publications (
    host TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    publication TEXT NOT NULL,
    slug TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    file TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (publication, slug)
);
CREATE TABLE IF NOT EXISTS flags (
    name TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS pushes (
    file TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    pushed_at TEXT NOT NULL
);
"""

# Posts with these statuses are finished and never processed again.
FINAL_STATUSES = ("done", "skipped")


class AlreadyRunning(Exception):
    pass


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._db = sqlite3.connect(path)
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Hold an exclusive lock for a whole run; raises AlreadyRunning if taken."""
        with open(self._path.with_suffix(".lock"), "w") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AlreadyRunning("Another run is in progress") from None
            yield

    def knows_publication(self, host: str) -> bool:
        return self._db.execute("SELECT 1 FROM publications WHERE host = ?", (host,)).fetchone() is not None

    def add_publication(self, host: str, at: datetime) -> None:
        with self._db:
            self._db.execute("INSERT OR IGNORE INTO publications VALUES (?, ?)", (host, at.isoformat()))

    def post_status(self, publication: str, slug: str) -> str | None:
        row = self._db.execute(
            "SELECT status FROM posts WHERE publication = ? AND slug = ?", (publication, slug)
        ).fetchone()
        return row[0] if row else None

    def post_urls(self, publication: str, status: str) -> list[str]:
        rows = self._db.execute(
            "SELECT url FROM posts WHERE publication = ? AND status = ? ORDER BY updated_at", (publication, status)
        )
        return [url for (url,) in rows]

    def record_post(
        self, publication: str, slug: str, *, url: str, status: str, at: datetime,
        reason: str | None = None, file: Path | None = None,
    ) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO posts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (publication, slug, url, status, reason, str(file) if file else None, at.isoformat()),
            )

    def pushed_size(self, file: str) -> int | None:
        """Size of the file when it was last pushed to the Boox, None if it never was."""
        row = self._db.execute("SELECT size FROM pushes WHERE file = ?", (file,)).fetchone()
        return row[0] if row else None

    def record_push(self, file: str, size: int, at: datetime) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO pushes VALUES (?, ?, ?)", (file, size, at.isoformat()))

    def has_flag(self, name: str) -> bool:
        return self._db.execute("SELECT 1 FROM flags WHERE name = ?", (name,)).fetchone() is not None

    def set_flag(self, name: str, on: bool) -> None:
        with self._db:
            if on:
                self._db.execute("INSERT OR IGNORE INTO flags VALUES (?)", (name,))
            else:
                self._db.execute("DELETE FROM flags WHERE name = ?", (name,))
