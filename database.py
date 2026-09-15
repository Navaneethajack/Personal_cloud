"""
database.py

SQLite metadata layer for the Personal Cloud Storage application.

IMPORTANT: SQLite stores ONLY metadata (name, object key, folder, size,
mime type, timestamps). Actual file contents always live in Backblaze B2.

Responsibilities:
    - Initialize the database and create tables/indexes
    - Insert / update / delete file metadata
    - Search files by name/folder/type
    - Create and list folders
    - Calculate aggregate usage from the local metadata cache
    - Rebuild metadata from a fresh B2 object listing (disaster recovery)

All queries use parameterized SQL. Raw user input is never interpolated
into a SQL string.
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("personal_cloud_storage.database")


class DatabaseError(Exception):
    """Raised when a database operation fails."""


@dataclass
class FileRecord:
    id: int
    name: str
    object_key: str
    folder: str
    size: int
    mime_type: str
    extension: str
    created_at: str
    updated_at: str


@dataclass
class FolderRecord:
    id: int
    name: str
    path: str
    created_at: str


def get_connection(db_path: str) -> sqlite3.Connection:
    """
    Open a new SQLite connection.

    A fresh connection is opened per call because Streamlit may run
    callbacks on different threads; sqlite3 connections are not safe to
    share across threads by default. Connections are short-lived and
    closed by callers (or used as a context manager).
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    """Create the database file and required tables/indexes if they don't exist."""
    conn = get_connection(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                object_key TEXT NOT NULL UNIQUE,
                folder TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                extension TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_files_object_key ON files(object_key);
            CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
            CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder);
            CREATE INDEX IF NOT EXISTS idx_files_created_at ON files(created_at);
            CREATE INDEX IF NOT EXISTS idx_folders_path ON folders(path);
            """
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.exception("Failed to initialize database")
        raise DatabaseError("Failed to initialize the local database.") from exc
    finally:
        conn.close()


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def insert_file(
    db_path: str,
    name: str,
    object_key: str,
    folder: str,
    size: int,
    mime_type: str,
    extension: str,
) -> int:
    """Insert a new file metadata record. Returns the new row id."""
    conn = get_connection(db_path)
    try:
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO files (name, object_key, folder, size, mime_type, extension, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, object_key, folder, size, mime_type, extension, now, now),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise DatabaseError(f"A file with object key '{object_key}' already exists in the database.") from exc
    except sqlite3.Error as exc:
        logger.exception("Failed to insert file metadata for %s", object_key)
        raise DatabaseError("Failed to save file metadata.") from exc
    finally:
        conn.close()


def get_file_by_key(db_path: str, object_key: str) -> Optional[FileRecord]:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM files WHERE object_key = ?", (object_key,)).fetchone()
        return _row_to_file(row) if row else None
    finally:
        conn.close()


def get_file_by_id(db_path: str, file_id: int) -> Optional[FileRecord]:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return _row_to_file(row) if row else None
    finally:
        conn.close()


def list_files_in_folder(db_path: str, folder: str) -> List[FileRecord]:
    """List all files directly inside the given folder (non-recursive)."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM files WHERE folder = ? ORDER BY name COLLATE NOCASE ASC",
            (folder,),
        ).fetchall()
        return [_row_to_file(r) for r in rows]
    finally:
        conn.close()


def list_all_files(db_path: str) -> List[FileRecord]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM files ORDER BY folder, name COLLATE NOCASE ASC").fetchall()
        return [_row_to_file(r) for r in rows]
    finally:
        conn.close()


def search_files(db_path: str, query: str) -> List[FileRecord]:
    """
    Search files by name, folder, or extension using the local metadata
    index (no need to list the entire B2 bucket on every keystroke).
    """
    conn = get_connection(db_path)
    try:
        like_query = f"%{query}%"
        rows = conn.execute(
            """
            SELECT * FROM files
            WHERE name LIKE ? COLLATE NOCASE
               OR folder LIKE ? COLLATE NOCASE
               OR extension LIKE ? COLLATE NOCASE
            ORDER BY name COLLATE NOCASE ASC
            """,
            (like_query, like_query, like_query),
        ).fetchall()
        return [_row_to_file(r) for r in rows]
    finally:
        conn.close()


def update_file_metadata(db_path: str, file_id: int, name: str, object_key: str, folder: str) -> None:
    """Update a file's name/object_key/folder (used for rename/move)."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE files SET name = ?, object_key = ?, folder = ?, updated_at = ? WHERE id = ?",
            (name, object_key, folder, _now(), file_id),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise DatabaseError(f"A file with object key '{object_key}' already exists.") from exc
    except sqlite3.Error as exc:
        logger.exception("Failed to update file metadata for id=%s", file_id)
        raise DatabaseError("Failed to update file metadata.") from exc
    finally:
        conn.close()


def delete_file(db_path: str, file_id: int) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()
    except sqlite3.Error as exc:
        logger.exception("Failed to delete file metadata for id=%s", file_id)
        raise DatabaseError("Failed to delete file metadata.") from exc
    finally:
        conn.close()


def delete_file_by_key(db_path: str, object_key: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM files WHERE object_key = ?", (object_key,))
        conn.commit()
    finally:
        conn.close()


def list_existing_names_in_folder(db_path: str, folder: str) -> List[str]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT name FROM files WHERE folder = ?", (folder,)).fetchall()
        return [r["name"] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def create_folder(db_path: str, name: str, path: str) -> Optional[int]:
    """Create a folder record. Returns None if it already exists."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO folders (name, path, created_at) VALUES (?, ?, ?)",
            (name, path, _now()),
        )
        conn.commit()
        return cur.lastrowid if cur.rowcount > 0 else None
    except sqlite3.Error as exc:
        logger.exception("Failed to create folder %s", path)
        raise DatabaseError("Failed to create folder.") from exc
    finally:
        conn.close()


def folder_exists(db_path: str, path: str) -> bool:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT 1 FROM folders WHERE path = ?", (path,)).fetchone()
        return row is not None
    finally:
        conn.close()


def list_subfolders(db_path: str, parent_path: str) -> List[FolderRecord]:
    """List immediate child folders of the given parent path."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM folders ORDER BY path ASC").fetchall()
        result = []
        for r in rows:
            path = r["path"]
            if parent_path == "":
                is_child = "/" not in path
            else:
                prefix = parent_path + "/"
                is_child = path.startswith(prefix) and "/" not in path[len(prefix):]
            if is_child:
                result.append(_row_to_folder(r))
        return result
    finally:
        conn.close()


def list_all_folders(db_path: str) -> List[FolderRecord]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM folders ORDER BY path ASC").fetchall()
        return [_row_to_folder(r) for r in rows]
    finally:
        conn.close()


def delete_folder(db_path: str, path: str) -> None:
    """
    Delete a folder record and metadata for any files/folders nested under
    it. Does NOT touch B2 directly -- callers must delete the underlying
    B2 objects first and only call this once that succeeds.
    """
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM folders WHERE path = ? OR path LIKE ?", (path, f"{path}/%"))
        conn.execute("DELETE FROM files WHERE folder = ? OR folder LIKE ?", (path, f"{path}/%"))
        conn.commit()
    except sqlite3.Error as exc:
        logger.exception("Failed to delete folder %s", path)
        raise DatabaseError("Failed to delete folder.") from exc
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Aggregate stats
# ---------------------------------------------------------------------------

def calculate_total_usage(db_path: str) -> int:
    """Sum of file sizes recorded in the local metadata cache."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COALESCE(SUM(size), 0) AS total FROM files").fetchone()
        return int(row["total"])
    finally:
        conn.close()


def get_stats(db_path: str) -> dict:
    """Return counts and category breakdown used by the storage dashboard."""
    conn = get_connection(db_path)
    try:
        file_count = conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
        folder_count = conn.execute("SELECT COUNT(*) AS c FROM folders").fetchone()["c"]
        total_size = conn.execute("SELECT COALESCE(SUM(size), 0) AS s FROM files").fetchone()["s"]
        largest = conn.execute(
            "SELECT name, folder, size FROM files ORDER BY size DESC LIMIT 10"
        ).fetchall()
        return {
            "file_count": file_count,
            "folder_count": folder_count,
            "total_size": int(total_size),
            "largest_files": [dict(r) for r in largest],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Recovery: rebuild metadata from B2
# ---------------------------------------------------------------------------

def rebuild_from_b2_objects(db_path: str, objects) -> dict:
    """
    Rebuild file and folder metadata from a fresh listing of B2 objects.

    `objects` is an iterable of objects exposing `.key`, `.size`, and
    `.last_modified` (see b2_storage.B2Object). Existing records for keys
    that still exist in B2 are preserved where possible; missing files are
    inserted; folder records are derived from key prefixes.

    This does not delete files from B2 -- it only repairs the local
    SQLite cache, and is safe to run at any time.
    """
    import utils  # local import to avoid a circular import at module load time

    conn = get_connection(db_path)
    inserted, updated, folders_created = 0, 0, 0
    try:
        seen_folders = set()
        for obj in objects:
            key = obj.key
            if key.endswith("/"):
                # Some tools create explicit zero-byte "folder marker" objects.
                # We treat folders purely as prefixes, so skip these as files
                # but still register the folder path.
                folder_path = key.rstrip("/")
                _ensure_folder_chain(conn, folder_path, seen_folders)
                continue

            if "/" in key:
                folder_path, name = key.rsplit("/", 1)
            else:
                folder_path, name = "", key

            _ensure_folder_chain(conn, folder_path, seen_folders)

            extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            mime_type = utils.guess_mime_type(name)
            now = _now()

            existing = conn.execute("SELECT id FROM files WHERE object_key = ?", (key,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE files SET name = ?, folder = ?, size = ?, mime_type = ?, "
                    "extension = ?, updated_at = ? WHERE object_key = ?",
                    (name, folder_path, obj.size, mime_type, extension, now, key),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO files (name, object_key, folder, size, mime_type, extension, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, key, folder_path, obj.size, mime_type, extension, now, now),
                )
                inserted += 1

        folders_created = len(seen_folders)
        conn.commit()
        return {"inserted": inserted, "updated": updated, "folders": folders_created}
    except sqlite3.Error as exc:
        conn.rollback()
        logger.exception("Failed to rebuild metadata from B2 listing")
        raise DatabaseError("Failed to rebuild the local database from Backblaze B2.") from exc
    finally:
        conn.close()


def _ensure_folder_chain(conn: sqlite3.Connection, folder_path: str, seen_folders: set) -> None:
    """Ensure every ancestor folder in a path has a folder record."""
    if not folder_path:
        return
    segments = folder_path.split("/")
    accumulated = ""
    for seg in segments:
        accumulated = seg if not accumulated else f"{accumulated}/{seg}"
        if accumulated in seen_folders:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO folders (name, path, created_at) VALUES (?, ?, ?)",
            (seg, accumulated, _now()),
        )
        seen_folders.add(accumulated)


def _row_to_file(row: sqlite3.Row) -> FileRecord:
    return FileRecord(
        id=row["id"],
        name=row["name"],
        object_key=row["object_key"],
        folder=row["folder"],
        size=row["size"],
        mime_type=row["mime_type"],
        extension=row["extension"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_folder(row: sqlite3.Row) -> FolderRecord:
    return FolderRecord(
        id=row["id"],
        name=row["name"],
        path=row["path"],
        created_at=row["created_at"],
    )
