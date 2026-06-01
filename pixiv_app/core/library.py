from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_LIBRARY_DB = "pixiv_app_library.db"


@dataclass
class DownloadFileRecord:
    page_index: int
    file_path: str
    source_url: str
    status: str
    size_bytes: int = 0
    content_hash: str = ""


class PixivLibrary:
    def __init__(self, db_path: str | Path = DEFAULT_LIBRARY_DB) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True) if self.db_path.parent != Path(".") else None
        # Allow sharing one connection across threads when guarded by a lock (e.g. TaskQueue).
        self.connection = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.setup()

    def close(self) -> None:
        self.connection.close()

    def setup(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS artists (
                    artist_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    account TEXT,
                    avatar_url TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS artworks (
                    artwork_id INTEGER PRIMARY KEY,
                    artist_id INTEGER,
                    type TEXT NOT NULL DEFAULT 'illust',
                    title TEXT NOT NULL DEFAULT '',
                    caption TEXT NOT NULL DEFAULT '',
                    page_count INTEGER NOT NULL DEFAULT 0,
                    bookmark_count INTEGER NOT NULL DEFAULT 0,
                    like_count INTEGER NOT NULL DEFAULT 0,
                    view_count INTEGER NOT NULL DEFAULT 0,
                    x_restrict INTEGER NOT NULL DEFAULT 0,
                    ai_type INTEGER NOT NULL DEFAULT 0,
                    thumbnail_url TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (artist_id) REFERENCES artists(artist_id)
                );

                CREATE TABLE IF NOT EXISTS tags (
                    tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    translated_name TEXT
                );

                CREATE TABLE IF NOT EXISTS artwork_tags (
                    artwork_id INTEGER NOT NULL,
                    tag_id INTEGER NOT NULL,
                    PRIMARY KEY (artwork_id, tag_id),
                    FOREIGN KEY (artwork_id) REFERENCES artworks(artwork_id) ON DELETE CASCADE,
                    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS artwork_files (
                    artwork_id INTEGER NOT NULL,
                    page_index INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'unknown',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (artwork_id, page_index),
                    FOREIGN KEY (artwork_id) REFERENCES artworks(artwork_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS download_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artwork_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    downloaded_pages INTEGER NOT NULL DEFAULT 0,
                    skipped_pages INTEGER NOT NULL DEFAULT 0,
                    failed_pages INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    started_at TEXT,
                    finished_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (artwork_id) REFERENCES artworks(artwork_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS thumbnail_cache (
                    artwork_id INTEGER NOT NULL,
                    page_index INTEGER NOT NULL,
                    source_path TEXT NOT NULL,
                    thumbnail_path TEXT NOT NULL,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    source_mtime REAL NOT NULL DEFAULT 0,
                    source_size INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (artwork_id, page_index),
                    FOREIGN KEY (artwork_id) REFERENCES artworks(artwork_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS favorites (
                    artwork_id INTEGER PRIMARY KEY,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (artwork_id) REFERENCES artworks(artwork_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS followed_artists (
                    artist_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_synced_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_artworks_artist_id ON artworks(artist_id);
                CREATE INDEX IF NOT EXISTS idx_artworks_type ON artworks(type);
                CREATE INDEX IF NOT EXISTS idx_download_history_artwork_id ON download_history(artwork_id);
                CREATE INDEX IF NOT EXISTS idx_artwork_files_path ON artwork_files(file_path);
                CREATE INDEX IF NOT EXISTS idx_thumbnail_cache_path ON thumbnail_cache(thumbnail_path);
                CREATE INDEX IF NOT EXISTS idx_favorites_created_at ON favorites(created_at);
                CREATE INDEX IF NOT EXISTS idx_followed_artists_enabled ON followed_artists(enabled);

                CREATE TABLE IF NOT EXISTS download_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    target_type TEXT NOT NULL,
                    target_id INTEGER,
                    page_index INTEGER,
                    task_kind TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 2,
                    status TEXT NOT NULL DEFAULT 'pending',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    leased_until REAL,
                    worker_id TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_download_tasks_status_pri_id
                    ON download_tasks(status, priority DESC, id ASC);

                CREATE TABLE IF NOT EXISTS artist_sync_watermark (
                    artist_id INTEGER PRIMARY KEY,
                    last_synced_illust_id INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
        self._apply_schema_migrations()

    def _apply_schema_migrations(self) -> None:
        """Incremental ALTER for existing databases created before new columns/tables."""
        with self.connection:
            cols = {row[1] for row in self.connection.execute("PRAGMA table_info(artwork_files)")}
            if "content_hash" not in cols:
                self.connection.execute(
                    "ALTER TABLE artwork_files ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"
                )

    def upsert_artwork(self, detail: dict[str, Any], *, artwork_type: str = "illust") -> None:
        source_id = _as_int(detail.get("illustId") or detail.get("id") or detail.get("novelId"))
        if source_id is None:
            return
        artwork_id = library_artwork_id(source_id, artwork_type)

        artist_id = _as_int(detail.get("userId"))
        if artist_id is not None:
            self._upsert_artist(detail, artist_id)

        tags = _extract_tags(detail)
        urls = detail.get("urls") if isinstance(detail.get("urls"), dict) else {}
        thumbnail_url = (
            detail.get("url")
            or detail.get("coverUrl")
            or urls.get("small")
            or urls.get("thumb")
            or urls.get("regular")
            or urls.get("original")
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO artworks (
                    artwork_id, artist_id, type, title, caption, page_count,
                    bookmark_count, like_count, view_count, x_restrict, ai_type,
                    thumbnail_url, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(artwork_id) DO UPDATE SET
                    artist_id = excluded.artist_id,
                    type = excluded.type,
                    title = excluded.title,
                    caption = excluded.caption,
                    page_count = excluded.page_count,
                    bookmark_count = excluded.bookmark_count,
                    like_count = excluded.like_count,
                    view_count = excluded.view_count,
                    x_restrict = excluded.x_restrict,
                    ai_type = excluded.ai_type,
                    thumbnail_url = excluded.thumbnail_url,
                    metadata_json = excluded.metadata_json,
                    created_at = COALESCE(excluded.created_at, artworks.created_at),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    artwork_id,
                    artist_id,
                    artwork_type,
                    str(detail.get("title") or ""),
                    str(detail.get("description") or detail.get("caption") or ""),
                    _as_int(detail.get("pageCount")) or 0,
                    _as_int(detail.get("bookmarkCount")) or 0,
                    _as_int(detail.get("likeCount")) or 0,
                    _as_int(detail.get("viewCount")) or 0,
                    _as_int(detail.get("xRestrict")) or 0,
                    _as_int(detail.get("aiType")) or 0,
                    str(thumbnail_url) if thumbnail_url else None,
                    json.dumps({**detail, "sourceId": source_id}, ensure_ascii=False),
                    str(detail.get("createDate") or detail.get("uploadDate") or "") or None,
                ),
            )
            self.connection.execute("DELETE FROM artwork_tags WHERE artwork_id = ?", (artwork_id,))
            for name, translated_name in tags:
                tag_id = self._upsert_tag(name, translated_name)
                self.connection.execute(
                    "INSERT OR IGNORE INTO artwork_tags (artwork_id, tag_id) VALUES (?, ?)",
                    (artwork_id, tag_id),
                )

    def ensure_artwork_stub(self, artwork_id: int, *, artwork_type: str = "illust", title: str = "") -> int:
        db_artwork_id = library_artwork_id(artwork_id, artwork_type)
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO artworks (artwork_id, type, title, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (db_artwork_id, artwork_type, title),
            )
        return db_artwork_id

    def ensure_artist_stub(self, artist_id: int, *, name: str = "") -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO artists (artist_id, name, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (artist_id, name or f"artist_{artist_id}"),
            )

    def set_artwork_artist(self, artwork_id: int, artist_id: int) -> None:
        with self.connection:
            self.ensure_artist_stub(artist_id)
            self.connection.execute(
                """
                UPDATE artworks
                SET artist_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE artwork_id = ?
                """,
                (artist_id, artwork_id),
            )

    def record_files(self, artwork_id: int, files: list[DownloadFileRecord]) -> None:
        with self.connection:
            for item in files:
                self.connection.execute(
                    """
                    INSERT INTO artwork_files (
                        artwork_id, page_index, file_path, source_url, status, size_bytes,
                        content_hash, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(artwork_id, page_index) DO UPDATE SET
                        file_path = excluded.file_path,
                        source_url = excluded.source_url,
                        status = excluded.status,
                        size_bytes = excluded.size_bytes,
                        content_hash = CASE
                            WHEN excluded.content_hash != '' THEN excluded.content_hash
                            ELSE artwork_files.content_hash
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        artwork_id,
                        item.page_index,
                        item.file_path,
                        item.source_url,
                        item.status,
                        item.size_bytes,
                        item.content_hash or "",
                    ),
                )

    def record_download_history(
        self,
        *,
        artwork_id: int,
        status: str,
        downloaded_pages: int,
        skipped_pages: int,
        failed_pages: int,
        message: str,
        started_at: str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO download_history (
                    artwork_id, status, downloaded_pages, skipped_pages, failed_pages, message, started_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (artwork_id, status, downloaded_pages, skipped_pages, failed_pages, message, started_at),
            )

    def update_sync_state(self, key: str, value: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO sync_state (key, value_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def upsert_thumbnail(
        self,
        *,
        artwork_id: int,
        page_index: int,
        source_path: str,
        thumbnail_path: str,
        width: int,
        height: int,
        source_mtime: float,
        source_size: int,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO thumbnail_cache (
                    artwork_id, page_index, source_path, thumbnail_path, width, height,
                    source_mtime, source_size, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(artwork_id, page_index) DO UPDATE SET
                    source_path = excluded.source_path,
                    thumbnail_path = excluded.thumbnail_path,
                    width = excluded.width,
                    height = excluded.height,
                    source_mtime = excluded.source_mtime,
                    source_size = excluded.source_size,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (artwork_id, page_index, source_path, thumbnail_path, width, height, source_mtime, source_size),
            )

    def set_favorite(self, artwork_id: int, favorite: bool, note: str = "") -> None:
        with self.connection:
            if favorite:
                self.connection.execute(
                    """
                    INSERT INTO favorites (artwork_id, note, created_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(artwork_id) DO UPDATE SET note = excluded.note
                    """,
                    (artwork_id, note),
                )
            else:
                self.connection.execute("DELETE FROM favorites WHERE artwork_id = ?", (artwork_id,))

    def set_followed_artist(self, artist_id: int, followed: bool, name: str = "") -> None:
        with self.connection:
            self.ensure_artist_stub(artist_id, name=name)
            if followed:
                self.connection.execute(
                    """
                    INSERT INTO followed_artists (artist_id, name, enabled, created_at)
                    VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(artist_id) DO UPDATE SET
                        name = COALESCE(NULLIF(excluded.name, ''), followed_artists.name),
                        enabled = 1
                    """,
                    (artist_id, name),
                )
            else:
                self.connection.execute(
                    "UPDATE followed_artists SET enabled = 0 WHERE artist_id = ?",
                    (artist_id,),
                )

    def mark_artist_synced(self, artist_id: int, *, error: str = "") -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO followed_artists (artist_id, enabled, last_synced_at, last_error)
                VALUES (?, 1, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(artist_id) DO UPDATE SET
                    last_synced_at = CURRENT_TIMESTAMP,
                    last_error = excluded.last_error
                """,
                (artist_id, error),
            )

    def _upsert_artist(self, detail: dict[str, Any], artist_id: int) -> None:
        self.connection.execute(
            """
            INSERT INTO artists (artist_id, name, account, avatar_url, metadata_json, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(artist_id) DO UPDATE SET
                name = excluded.name,
                account = COALESCE(excluded.account, artists.account),
                avatar_url = COALESCE(excluded.avatar_url, artists.avatar_url),
                metadata_json = excluded.metadata_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                artist_id,
                str(detail.get("userName") or detail.get("artistName") or f"artist_{artist_id}"),
                str(detail.get("userAccount") or "") or None,
                str(detail.get("profileImageUrl") or detail.get("userImage") or "") or None,
                json.dumps(
                    {
                        "userId": artist_id,
                        "userName": detail.get("userName"),
                        "userAccount": detail.get("userAccount"),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    def _upsert_tag(self, name: str, translated_name: str | None) -> int:
        self.connection.execute(
            """
            INSERT INTO tags (name, translated_name)
            VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET
                translated_name = COALESCE(excluded.translated_name, tags.translated_name)
            """,
            (name, translated_name),
        )
        row = self.connection.execute("SELECT tag_id FROM tags WHERE name = ?", (name,)).fetchone()
        return int(row["tag_id"])


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def library_artwork_id(source_id: int, artwork_type: str = "illust") -> int:
    if artwork_type == "novel":
        return -abs(int(source_id))
    return int(source_id)


def _extract_tags(detail: dict[str, Any]) -> list[tuple[str, str | None]]:
    raw_tags = detail.get("tags")
    if isinstance(raw_tags, dict):
        raw_tags = raw_tags.get("tags")
    if not isinstance(raw_tags, list):
        return []

    result: list[tuple[str, str | None]] = []
    for item in raw_tags:
        if isinstance(item, str):
            result.append((item, None))
            continue
        if not isinstance(item, dict):
            continue
        name = item.get("tag") or item.get("name")
        if not name:
            continue
        translation = item.get("translation")
        translated_name = None
        if isinstance(translation, dict):
            translated_name = translation.get("en") or translation.get("zh") or translation.get("romaji")
        elif isinstance(translation, str):
            translated_name = translation
        result.append((str(name), str(translated_name) if translated_name else None))
    return result
