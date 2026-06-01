from __future__ import annotations

import json
import sqlite3
import threading
import time
from pixiv_app.tasks.models import DownloadTaskSpec


class TaskQueue:
    """SQLite-backed queue with lease/reclaim; thread-safe for concurrent workers."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def enqueue(self, tasks: list[DownloadTaskSpec]) -> tuple[int, int]:
        inserted = 0
        with self._lock:
            with self._conn:
                for t in tasks:
                    fp = t.fingerprint()
                    cur = self._conn.execute(
                        """
                        INSERT OR IGNORE INTO download_tasks (
                            fingerprint, target_type, target_id, page_index, task_kind,
                            priority, status, payload_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                        """,
                        (
                            fp,
                            t.target_type,
                            t.target_id,
                            t.page_index,
                            t.task_kind,
                            int(t.priority),
                            json.dumps(t.payload, ensure_ascii=False),
                        ),
                    )
                    inserted += cur.rowcount
        skipped = len(tasks) - inserted
        return inserted, skipped

    def requeue_failed_by_fingerprints(self, fingerprints: list[str]) -> int:
        """Reset `failed` rows to `pending` so a retry can pick them up."""
        if not fingerprints:
            return 0
        placeholders = ",".join("?" * len(fingerprints))
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    f"""
                    UPDATE download_tasks
                    SET status = 'pending',
                        leased_until = NULL,
                        worker_id = NULL,
                        last_error = '',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE fingerprint IN ({placeholders}) AND status = 'failed'
                    """,
                    fingerprints,
                )
                return int(cur.rowcount or 0)

    def count_incomplete_for_fingerprints(self, fingerprints: list[str]) -> int:
        """Rows for these fingerprints that are not successfully finished (`done`)."""
        if not fingerprints:
            return 0
        placeholders = ",".join("?" * len(fingerprints))
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) FROM download_tasks
                WHERE fingerprint IN ({placeholders}) AND status != 'done'
                """,
                fingerprints,
            ).fetchone()
        return int(row[0]) if row else 0

    def lease(
        self,
        worker_id: str,
        *,
        limit: int = 4,
        lease_seconds: float = 180.0,
    ) -> list[int]:
        """Grab up to `limit` tasks; reclaims expired leases."""
        now = time.time()
        deadline = now + lease_seconds
        ids: list[int] = []
        with self._lock:
            with self._conn:
                for _ in range(limit):
                    cur = self._conn.execute(
                        """
                        WITH picked AS (
                            SELECT id FROM download_tasks
                            WHERE status = 'pending'
                               OR (status = 'running' AND leased_until IS NOT NULL AND leased_until < ?)
                            ORDER BY priority DESC, id ASC
                            LIMIT 1
                        )
                        UPDATE download_tasks
                        SET status = 'running',
                            worker_id = ?,
                            leased_until = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id IN (SELECT id FROM picked)
                        RETURNING id
                        """,
                        (now, worker_id, deadline),
                    )
                    row = cur.fetchone()
                    if row is None:
                        break
                    ids.append(int(row[0]))
        return ids

    def load_task(self, task_id: int) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM download_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return row

    def mark_done(self, task_id: int) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE download_tasks
                    SET status = 'done',
                        leased_until = NULL,
                        worker_id = NULL,
                        last_error = '',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (task_id,),
                )

    def mark_failed_final(self, task_id: int, message: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE download_tasks
                    SET status = 'failed',
                        leased_until = NULL,
                        worker_id = NULL,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (message[:2000], task_id),
                )

    def requeue_retry(self, task_id: int, message: str, max_attempts: int = 4) -> bool:
        """Return True if scheduled for another attempt."""
        with self._lock:
            with self._conn:
                row = self._conn.execute(
                    "SELECT attempts FROM download_tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if row is None:
                    return False
                attempts = int(row[0]) + 1
                if attempts >= max_attempts:
                    self._conn.execute(
                        """
                        UPDATE download_tasks
                        SET status = 'failed',
                            attempts = ?,
                            leased_until = NULL,
                            worker_id = NULL,
                            last_error = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (attempts, message[:2000], task_id),
                    )
                    return False
                self._conn.execute(
                    """
                    UPDATE download_tasks
                    SET status = 'pending',
                        attempts = ?,
                        leased_until = NULL,
                        worker_id = NULL,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (attempts, message[:2000], task_id),
                )
                return True

    def count_by_status(self, status: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM download_tasks WHERE status = ?",
                (status,),
            ).fetchone()
        return int(row[0]) if row else 0

    def snapshot_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT status, COUNT(*) FROM download_tasks GROUP BY status
                """
            ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}
