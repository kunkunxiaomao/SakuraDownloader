from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pixiv_app.core.library import DEFAULT_LIBRARY_DB, PixivLibrary


DEFAULT_THUMBNAIL_DIR = "runtime/thumbnails"
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


@dataclass
class ThumbnailResult:
    artwork_id: int
    page_index: int
    source_path: str
    thumbnail_path: str
    width: int
    height: int
    generated: bool


class ThumbnailCache:
    def __init__(
        self,
        *,
        db_path: str | Path = DEFAULT_LIBRARY_DB,
        cache_dir: str | Path = DEFAULT_THUMBNAIL_DIR,
        size: tuple[int, int] = (360, 360),
        quality: int = 86,
    ) -> None:
        self.db_path = Path(db_path)
        self.cache_dir = Path(cache_dir)
        self.size = size
        self.quality = quality
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def ensure_for_artwork(self, artwork_id: int) -> list[ThumbnailResult]:
        library = PixivLibrary(self.db_path)
        try:
            rows = library.connection.execute(
                """
                SELECT artwork_id, page_index, file_path
                FROM artwork_files
                WHERE artwork_id = ?
                ORDER BY page_index ASC
                """,
                (artwork_id,),
            ).fetchall()
            return [result for row in rows if (result := self.ensure_for_file(library, row)) is not None]
        finally:
            library.close()

    def build_missing(self, *, limit: int = 200) -> list[ThumbnailResult]:
        library = PixivLibrary(self.db_path)
        try:
            rows = library.connection.execute(
                """
                SELECT f.artwork_id, f.page_index, f.file_path
                FROM artwork_files f
                LEFT JOIN thumbnail_cache t
                  ON t.artwork_id = f.artwork_id AND t.page_index = f.page_index
                WHERE t.thumbnail_path IS NULL
                   OR t.source_path != f.file_path
                ORDER BY f.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [result for row in rows if (result := self.ensure_for_file(library, row)) is not None]
        finally:
            library.close()

    def ensure_for_file(self, library: PixivLibrary, row) -> ThumbnailResult | None:
        source_path = Path(str(row["file_path"]))
        if source_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES or not source_path.exists():
            return None
        stat = source_path.stat()
        thumbnail_path = self._thumbnail_path(int(row["artwork_id"]), int(row["page_index"]), source_path)

        cached = library.connection.execute(
            """
            SELECT thumbnail_path, width, height, source_mtime, source_size
            FROM thumbnail_cache
            WHERE artwork_id = ? AND page_index = ?
            """,
            (int(row["artwork_id"]), int(row["page_index"])),
        ).fetchone()
        if (
            cached
            and Path(str(cached["thumbnail_path"])).exists()
            and float(cached["source_mtime"]) == stat.st_mtime
            and int(cached["source_size"]) == stat.st_size
        ):
            return ThumbnailResult(
                artwork_id=int(row["artwork_id"]),
                page_index=int(row["page_index"]),
                source_path=str(source_path),
                thumbnail_path=str(cached["thumbnail_path"]),
                width=int(cached["width"]),
                height=int(cached["height"]),
                generated=False,
            )

        width, height = self._generate(source_path, thumbnail_path)
        library.upsert_thumbnail(
            artwork_id=int(row["artwork_id"]),
            page_index=int(row["page_index"]),
            source_path=str(source_path),
            thumbnail_path=str(thumbnail_path),
            width=width,
            height=height,
            source_mtime=stat.st_mtime,
            source_size=stat.st_size,
        )
        return ThumbnailResult(
            artwork_id=int(row["artwork_id"]),
            page_index=int(row["page_index"]),
            source_path=str(source_path),
            thumbnail_path=str(thumbnail_path),
            width=width,
            height=height,
            generated=True,
        )

    def _generate(self, source_path: Path, thumbnail_path: Path) -> tuple[int, int]:
        try:
            from PIL import Image, ImageOps
        except ImportError as exc:
            raise RuntimeError("Pillow is required to generate thumbnails. Install it with: pip install Pillow") from exc

        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(self.size, Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.save(thumbnail_path, format="JPEG", quality=self.quality, optimize=True)
            return image.size

    def _thumbnail_path(self, artwork_id: int, page_index: int, source_path: Path) -> Path:
        bucket = str(abs(artwork_id) % 1000).zfill(3)
        return self.cache_dir / bucket / f"{artwork_id}_{page_index}_{source_path.stem}.jpg"
