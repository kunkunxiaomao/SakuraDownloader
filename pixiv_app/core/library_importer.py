from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pixiv_app.core.library import DEFAULT_LIBRARY_DB, DownloadFileRecord, SakuraLibrary


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
ILLUST_FILE_PATTERN = re.compile(r"(?P<artwork_id>\d+)_p(?P<page_index>\d+)", re.IGNORECASE)


@dataclass
class ImportSummary:
    scanned_files: int = 0
    imported_files: int = 0
    skipped_files: int = 0
    imported_artworks: int = 0


class LegacyDownloadImporter:
    def __init__(self, *, db_path: str | Path = DEFAULT_LIBRARY_DB) -> None:
        self.db_path = Path(db_path)

    def scan(self, root: str | Path = "Sakura_Downloads") -> ImportSummary:
        root_path = Path(root)
        summary = ImportSummary()
        if not root_path.exists():
            return summary

        library = SakuraLibrary(self.db_path)
        imported_artworks: set[int] = set()
        try:
            for file_path in root_path.rglob("*"):
                if not file_path.is_file() or file_path.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                summary.scanned_files += 1
                match = ILLUST_FILE_PATTERN.search(file_path.stem)
                if match is None:
                    summary.skipped_files += 1
                    continue
                artwork_id = int(match.group("artwork_id"))
                page_index = int(match.group("page_index"))
                library.ensure_artwork_stub(artwork_id, artwork_type="illust", title=str(artwork_id))
                artist_id = self._infer_artist_id(root_path, file_path)
                if artist_id is not None:
                    library.set_artwork_artist(artwork_id, artist_id)
                library.record_files(
                    artwork_id,
                    [
                        DownloadFileRecord(
                            page_index=page_index,
                            file_path=str(file_path.resolve()),
                            source_url="",
                            status="imported",
                            size_bytes=file_path.stat().st_size,
                        )
                    ],
                )
                imported_artworks.add(artwork_id)
                summary.imported_files += 1
            summary.imported_artworks = len(imported_artworks)
            return summary
        finally:
            library.close()

    def _infer_artist_id(self, root_path: Path, file_path: Path) -> int | None:
        try:
            relative = file_path.relative_to(root_path)
        except ValueError:
            return None
        if not relative.parts:
            return None
        first = relative.parts[0]
        return int(first) if first.isdigit() else None
