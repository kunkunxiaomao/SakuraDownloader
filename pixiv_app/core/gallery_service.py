from __future__ import annotations

from pathlib import Path
from typing import Any

from pixiv_app.core.library_importer import LegacyDownloadImporter
from pixiv_app.core.library import DEFAULT_LIBRARY_DB, SakuraLibrary
from pixiv_app.core.thumbnails import DEFAULT_THUMBNAIL_DIR, ThumbnailCache


class LocalGalleryService:
    def __init__(
        self,
        *,
        db_path: str | Path = DEFAULT_LIBRARY_DB,
        thumbnail_dir: str | Path = DEFAULT_THUMBNAIL_DIR,
    ) -> None:
        self.db_path = Path(db_path)
        self.thumbnail_cache = ThumbnailCache(db_path=self.db_path, cache_dir=thumbnail_dir)

    def list_artworks(
        self,
        *,
        query: str = "",
        tag: str = "",
        artist_id: int | None = None,
        artwork_type: str = "",
        include_restricted: bool = True,
        limit: int = 60,
        offset: int = 0,
        ensure_thumbnails: bool = True,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        library = SakuraLibrary(self.db_path)
        try:
            where: list[str] = []
            params: list[Any] = []
            joins = """
                LEFT JOIN artists ar ON ar.artist_id = a.artist_id
                LEFT JOIN thumbnail_cache tc ON tc.artwork_id = a.artwork_id AND tc.page_index = 0
                LEFT JOIN artwork_files af ON af.artwork_id = a.artwork_id AND af.page_index = 0
            """
            if query:
                like = f"%{query}%"
                where.append("(a.title LIKE ? OR a.caption LIKE ? OR ar.name LIKE ?)")
                params.extend([like, like, like])
            if tag:
                joins += """
                    JOIN artwork_tags at_filter ON at_filter.artwork_id = a.artwork_id
                    JOIN tags t_filter ON t_filter.tag_id = at_filter.tag_id
                """
                where.append("t_filter.name = ?")
                params.append(tag)
            if artist_id is not None:
                where.append("a.artist_id = ?")
                params.append(int(artist_id))
            if artwork_type:
                where.append("a.type = ?")
                params.append(artwork_type)
            if not include_restricted:
                where.append("a.x_restrict = 0")

            where_sql = "WHERE " + " AND ".join(where) if where else ""
            total = library.connection.execute(
                f"SELECT COUNT(DISTINCT a.artwork_id) AS total FROM artworks a {joins} {where_sql}",
                params,
            ).fetchone()["total"]
            rows = library.connection.execute(
                f"""
                SELECT
                    a.artwork_id,
                    a.type,
                    a.title,
                    a.artist_id,
                    ar.name AS artist_name,
                    a.page_count,
                    a.bookmark_count,
                    a.like_count,
                    a.view_count,
                    a.x_restrict,
                    a.ai_type,
                    a.thumbnail_url,
                    CASE WHEN fav.artwork_id IS NULL THEN 0 ELSE 1 END AS is_favorite,
                    COALESCE(tc.thumbnail_path, '') AS thumbnail_path,
                    COALESCE(af.file_path, '') AS primary_file_path,
                    a.updated_at
                FROM artworks a
                {joins}
                LEFT JOIN favorites fav ON fav.artwork_id = a.artwork_id
                {where_sql}
                GROUP BY a.artwork_id
                ORDER BY a.updated_at DESC, a.artwork_id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

            items = [dict(row) for row in rows]
            if ensure_thumbnails:
                self._ensure_list_thumbnails(items)
                items = self._refresh_thumbnail_paths(library, items)
            return {"total": int(total), "limit": limit, "offset": offset, "items": items}
        finally:
            library.close()

    def get_artwork(self, artwork_id: int, *, ensure_thumbnails: bool = True) -> dict[str, Any] | None:
        if ensure_thumbnails:
            self.thumbnail_cache.ensure_for_artwork(artwork_id)
        library = SakuraLibrary(self.db_path)
        try:
            row = library.connection.execute(
                """
                SELECT a.*, ar.name AS artist_name, ar.account AS artist_account
                     , CASE WHEN fav.artwork_id IS NULL THEN 0 ELSE 1 END AS is_favorite
                FROM artworks a
                LEFT JOIN artists ar ON ar.artist_id = a.artist_id
                LEFT JOIN favorites fav ON fav.artwork_id = a.artwork_id
                WHERE a.artwork_id = ?
                """,
                (artwork_id,),
            ).fetchone()
            if row is None:
                return None
            files = library.connection.execute(
                """
                SELECT f.*, tc.thumbnail_path, tc.width AS thumbnail_width, tc.height AS thumbnail_height
                FROM artwork_files f
                LEFT JOIN thumbnail_cache tc
                  ON tc.artwork_id = f.artwork_id AND tc.page_index = f.page_index
                WHERE f.artwork_id = ?
                ORDER BY f.page_index ASC
                """,
                (artwork_id,),
            ).fetchall()
            tags = library.connection.execute(
                """
                SELECT t.name, t.translated_name
                FROM tags t
                JOIN artwork_tags at ON at.tag_id = t.tag_id
                WHERE at.artwork_id = ?
                ORDER BY t.name ASC
                """,
                (artwork_id,),
            ).fetchall()
            history = library.connection.execute(
                """
                SELECT status, downloaded_pages, skipped_pages, failed_pages, message, finished_at
                FROM download_history
                WHERE artwork_id = ?
                ORDER BY finished_at DESC
                LIMIT 20
                """,
                (artwork_id,),
            ).fetchall()
            return {
                "artwork": dict(row),
                "files": [dict(item) for item in files],
                "tags": [dict(item) for item in tags],
                "history": [dict(item) for item in history],
            }
        finally:
            library.close()

    def list_tags(self, *, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        library = SakuraLibrary(self.db_path)
        try:
            params: list[Any] = []
            where = ""
            if query:
                where = "WHERE t.name LIKE ? OR t.translated_name LIKE ?"
                params.extend([f"%{query}%", f"%{query}%"])
            rows = library.connection.execute(
                f"""
                SELECT t.name, t.translated_name, COUNT(at.artwork_id) AS artwork_count
                FROM tags t
                LEFT JOIN artwork_tags at ON at.tag_id = t.tag_id
                {where}
                GROUP BY t.tag_id
                ORDER BY artwork_count DESC, t.name ASC
                LIMIT ?
                """,
                [*params, max(1, min(int(limit), 500))],
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            library.close()

    def list_artists(self, *, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        library = SakuraLibrary(self.db_path)
        try:
            params: list[Any] = []
            where = ""
            if query:
                where = "WHERE ar.name LIKE ? OR ar.account LIKE ?"
                params.extend([f"%{query}%", f"%{query}%"])
            rows = library.connection.execute(
                f"""
                SELECT ar.artist_id, ar.name, ar.account, COUNT(a.artwork_id) AS artwork_count
                FROM artists ar
                LEFT JOIN artworks a ON a.artist_id = ar.artist_id
                {where}
                GROUP BY ar.artist_id
                ORDER BY artwork_count DESC, ar.name ASC
                LIMIT ?
                """,
                [*params, max(1, min(int(limit), 500))],
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            library.close()

    def get_artist(self, artist_id: int, *, limit: int = 60, offset: int = 0) -> dict[str, Any] | None:
        library = SakuraLibrary(self.db_path)
        try:
            row = library.connection.execute(
                """
                SELECT ar.*, COUNT(a.artwork_id) AS artwork_count,
                       CASE WHEN fa.enabled = 1 THEN 1 ELSE 0 END AS is_followed,
                       fa.last_synced_at, fa.last_error
                FROM artists ar
                LEFT JOIN artworks a ON a.artist_id = ar.artist_id
                LEFT JOIN followed_artists fa ON fa.artist_id = ar.artist_id
                WHERE ar.artist_id = ?
                GROUP BY ar.artist_id
                """,
                (artist_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "artist": dict(row),
                "artworks": self.list_artworks(artist_id=artist_id, limit=limit, offset=offset),
            }
        finally:
            library.close()

    def list_favorites(self, *, limit: int = 60, offset: int = 0) -> dict[str, Any]:
        library = SakuraLibrary(self.db_path)
        try:
            total = library.connection.execute("SELECT COUNT(*) AS total FROM favorites").fetchone()["total"]
            rows = library.connection.execute(
                """
                SELECT
                    a.artwork_id, a.type, a.title, a.artist_id, ar.name AS artist_name,
                    a.page_count, a.bookmark_count, a.like_count, a.view_count,
                    a.x_restrict, a.ai_type, a.thumbnail_url,
                    1 AS is_favorite,
                    COALESCE(tc.thumbnail_path, '') AS thumbnail_path,
                    COALESCE(af.file_path, '') AS primary_file_path,
                    fav.created_at AS favorite_at
                FROM favorites fav
                JOIN artworks a ON a.artwork_id = fav.artwork_id
                LEFT JOIN artists ar ON ar.artist_id = a.artist_id
                LEFT JOIN thumbnail_cache tc ON tc.artwork_id = a.artwork_id AND tc.page_index = 0
                LEFT JOIN artwork_files af ON af.artwork_id = a.artwork_id AND af.page_index = 0
                ORDER BY fav.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (max(1, min(int(limit), 200)), max(0, int(offset))),
            ).fetchall()
            items = [dict(row) for row in rows]
            self._ensure_list_thumbnails(items)
            items = self._refresh_thumbnail_paths(library, items)
            return {"total": int(total), "limit": limit, "offset": offset, "items": items}
        finally:
            library.close()

    def set_favorite(self, artwork_id: int, favorite: bool, note: str = "") -> dict[str, Any]:
        library = SakuraLibrary(self.db_path)
        try:
            library.set_favorite(artwork_id, favorite, note)
            return {"artwork_id": artwork_id, "favorite": favorite}
        finally:
            library.close()

    def list_followed_artists(self) -> list[dict[str, Any]]:
        library = SakuraLibrary(self.db_path)
        try:
            rows = library.connection.execute(
                """
                SELECT fa.artist_id, COALESCE(NULLIF(fa.name, ''), ar.name, '') AS name,
                       fa.enabled, fa.last_synced_at, fa.last_error,
                       COUNT(a.artwork_id) AS artwork_count
                FROM followed_artists fa
                LEFT JOIN artists ar ON ar.artist_id = fa.artist_id
                LEFT JOIN artworks a ON a.artist_id = fa.artist_id
                WHERE fa.enabled = 1
                GROUP BY fa.artist_id
                ORDER BY name ASC, fa.artist_id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            library.close()

    def set_followed_artist(self, artist_id: int, followed: bool, name: str = "") -> dict[str, Any]:
        library = SakuraLibrary(self.db_path)
        try:
            library.set_followed_artist(artist_id, followed, name)
            return {"artist_id": artist_id, "followed": followed}
        finally:
            library.close()

    def scan_legacy_downloads(self, *, root: str | Path = "Sakura_Downloads") -> dict[str, Any]:
        summary = LegacyDownloadImporter(db_path=self.db_path).scan(root)
        self.thumbnail_cache.build_missing(limit=500)
        return summary.__dict__

    def build_missing_thumbnails(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return [item.__dict__ for item in self.thumbnail_cache.build_missing(limit=limit)]

    def get_thumbnail_path(self, artwork_id: int, *, page_index: int = 0) -> Path | None:
        self.thumbnail_cache.ensure_for_artwork(artwork_id)
        library = SakuraLibrary(self.db_path)
        try:
            row = library.connection.execute(
                """
                SELECT thumbnail_path
                FROM thumbnail_cache
                WHERE artwork_id = ? AND page_index = ?
                """,
                (artwork_id, page_index),
            ).fetchone()
            if row is None:
                return None
            path = Path(str(row["thumbnail_path"]))
            return path if path.exists() else None
        finally:
            library.close()

    def get_source_file_path(self, artwork_id: int, *, page_index: int = 0) -> Path | None:
        library = SakuraLibrary(self.db_path)
        try:
            row = library.connection.execute(
                """
                SELECT file_path
                FROM artwork_files
                WHERE artwork_id = ? AND page_index = ?
                """,
                (artwork_id, page_index),
            ).fetchone()
            if row is None:
                return None
            path = Path(str(row["file_path"]))
            return path if path.exists() else None
        finally:
            library.close()

    def _ensure_list_thumbnails(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            if item.get("thumbnail_path"):
                continue
            if not item.get("primary_file_path"):
                continue
            self.thumbnail_cache.ensure_for_artwork(int(item["artwork_id"]))

    def _refresh_thumbnail_paths(self, library: SakuraLibrary, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        artwork_ids = [int(item["artwork_id"]) for item in items]
        if not artwork_ids:
            return items
        placeholders = ",".join("?" for _ in artwork_ids)
        rows = library.connection.execute(
            f"""
            SELECT artwork_id, thumbnail_path
            FROM thumbnail_cache
            WHERE page_index = 0 AND artwork_id IN ({placeholders})
            """,
            artwork_ids,
        ).fetchall()
        thumbs = {int(row["artwork_id"]): str(row["thumbnail_path"]) for row in rows}
        for item in items:
            item["thumbnail_path"] = thumbs.get(int(item["artwork_id"]), item.get("thumbnail_path", ""))
        return items
