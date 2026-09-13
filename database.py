import os
import sqlite3
from datetime import UTC, datetime
from typing import Any

DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "uploads.db"),
)


def get_db_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT UNIQUE NOT NULL,
                    original_filename TEXT NOT NULL,
                    stored_filename TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    user_token TEXT NOT NULL,
                    manage_token TEXT UNIQUE NOT NULL,
                    delete_token TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    downloads_count INTEGER DEFAULT 0
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_slug ON files(slug);")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_user_token ON files(user_token);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_manage_token ON files(manage_token);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_delete_token ON files(delete_token);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_expires_at ON files(expires_at);"
            )
    finally:
        conn.close()


def save_file_record(
    slug: str,
    original_filename: str,
    stored_filename: str,
    file_size: int,
    content_type: str,
    user_token: str,
    manage_token: str,
    delete_token: str,
    created_at: datetime,
    expires_at: datetime,
) -> int:
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO files (
                    slug, original_filename, stored_filename, file_size, content_type,
                    user_token, manage_token, delete_token, created_at, expires_at, downloads_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    slug,
                    original_filename,
                    stored_filename,
                    file_size,
                    content_type,
                    user_token,
                    manage_token,
                    delete_token,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_file_by_slug(slug: str) -> dict[str, Any] | None:
    conn = get_db_connection()
    try:
        cur = conn.execute("SELECT * FROM files WHERE slug = ?", (slug,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_file_by_manage_token(token: str) -> dict[str, Any] | None:
    conn = get_db_connection()
    try:
        cur = conn.execute("SELECT * FROM files WHERE manage_token = ?", (token,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_file_by_delete_token(token: str) -> dict[str, Any] | None:
    conn = get_db_connection()
    try:
        cur = conn.execute("SELECT * FROM files WHERE delete_token = ?", (token,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_files_by_user(user_token: str) -> list[dict[str, Any]]:
    now_iso = datetime.now(UTC).isoformat()
    conn = get_db_connection()
    try:
        cur = conn.execute(
            """
            SELECT * FROM files
            WHERE user_token = ? AND expires_at > ?
            ORDER BY id DESC
            """,
            (user_token, now_iso),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def update_file_slug(file_id: int, new_slug: str) -> bool:
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("UPDATE files SET slug = ? WHERE id = ?", (new_slug, file_id))
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def update_file_expiry(file_id: int, new_expires_at: datetime) -> None:
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE files SET expires_at = ? WHERE id = ?",
                (new_expires_at.isoformat(), file_id),
            )
    finally:
        conn.close()


def update_file_content(
    file_id: int,
    original_filename: str,
    stored_filename: str,
    file_size: int,
    content_type: str,
) -> None:
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                UPDATE files
                SET original_filename = ?, stored_filename = ?, file_size = ?, content_type = ?
                WHERE id = ?
                """,
                (original_filename, stored_filename, file_size, content_type, file_id),
            )
    finally:
        conn.close()


def increment_download_count(file_id: int) -> None:
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE files SET downloads_count = downloads_count + 1 WHERE id = ?",
                (file_id,),
            )
    finally:
        conn.close()


def delete_file_record(file_id: int) -> str | None:
    """Deletes DB record and returns the stored_filename so caller can delete physical file."""
    conn = get_db_connection()
    try:
        with conn:
            cur = conn.execute(
                "SELECT stored_filename FROM files WHERE id = ?", (file_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            stored_filename = row["stored_filename"]
            conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
            return stored_filename
    finally:
        conn.close()


def cleanup_expired_files(upload_dir: str) -> int:
    """Removes expired files from DB and deletes corresponding physical files."""
    now_iso = datetime.now(UTC).isoformat()
    conn = get_db_connection()
    deleted_count = 0
    try:
        cur = conn.execute(
            "SELECT id, stored_filename FROM files WHERE expires_at <= ?", (now_iso,)
        )
        expired = cur.fetchall()
        if not expired:
            return 0

        with conn:
            for item in expired:
                file_id = item["id"]
                stored_filename = item["stored_filename"]
                conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
                deleted_count += 1
                if upload_dir and stored_filename:
                    file_path = os.path.join(upload_dir, stored_filename)
                    if os.path.isfile(file_path):
                        try:
                            os.remove(file_path)
                        except OSError:
                            pass
        return deleted_count
    finally:
        conn.close()
