from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Literal


class TaskPriority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


TaskStatus = Literal["pending", "running", "done", "failed"]


@dataclass(frozen=True)
class DownloadTaskSpec:
    """Serializable unit stored in SQLite via TaskQueue (payload omits secrets)."""

    target_type: Literal["illust", "novel"]
    target_id: int
    task_kind: str
    priority: TaskPriority
    page_index: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        page_part = self.page_index if self.page_index is not None else -1
        return f"{self.task_kind}:{self.target_type}:{self.target_id}:{page_part}"


@dataclass
class ParsedLine:
    """One logical input token after splitting (before expansion to illust ids)."""

    category: Literal["user", "illust", "novel", "tag", "keyword"]
    id_value: int
    tag: str = ""
    keyword_text: str = ""
    raw_token: str = ""
    pixiv_kind: str = "user"


@dataclass
class ParsePreview:
    """Statistics for GUI preview."""

    total_lines: int
    by_category: dict[str, int]
