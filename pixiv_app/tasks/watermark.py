from __future__ import annotations

import sqlite3


def get_last_synced_illust_id(conn: sqlite3.Connection, artist_id: int) -> int:
    row = conn.execute(
        "SELECT last_synced_illust_id FROM artist_sync_watermark WHERE artist_id = ?",
        (artist_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def advance_watermark(conn: sqlite3.Connection, artist_id: int, illust_id: int) -> None:
    """Raise watermark to at least illust_id for incremental author sync."""
    with conn:
        conn.execute(
            """
            INSERT INTO artist_sync_watermark (artist_id, last_synced_illust_id, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(artist_id) DO UPDATE SET
                last_synced_illust_id = MAX(
                    artist_sync_watermark.last_synced_illust_id,
                    excluded.last_synced_illust_id
                ),
                updated_at = CURRENT_TIMESTAMP
            """,
            (artist_id, illust_id),
        )


def filter_newer_than_watermark(work_ids: list[int], watermark: int) -> list[int]:
    """Keep illust ids strictly greater than last synced watermark (Pixiv ids grow over time)."""
    return [wid for wid in work_ids if wid > watermark]
