from __future__ import annotations

import re
from typing import Literal

from pixiv_app.tasks.models import ParsePreview, ParsedLine


def split_input_blob(blob: str) -> list[str]:
    parts = re.split(r"[\s,，;；\n\r]+", blob.strip())
    return [p for p in parts if p]


def parse_batch_for_mode(
    raw: str,
    *,
    ui_mode: Literal["user", "illust", "novel", "keyword"],
) -> tuple[list[ParsedLine], ParsePreview]:
    """
    Parse multi-line / comma / space separated input into structured intents.
    Legacy stub — delegates to generic tokenizer; platform-specific parsing
    is handled by plugins at runtime.
    """
    tokens = split_input_blob(raw)
    lines: list[ParsedLine] = []
    stats: dict[str, int] = {}

    def bump(key: str) -> None:
        stats[key] = stats.get(key, 0) + 1

    for token in tokens:
        t = token.strip()
        if not t:
            continue
        if t.startswith("#"):
            tag = t[1:].strip()
            if tag:
                lines.append(ParsedLine(category="tag", id_value=0, tag=tag, raw_token=t))
                bump("tag")
            continue

        # Generic fallback: store as unknown keyword token
        lines.append(ParsedLine(category="keyword", id_value=0, keyword_text=t, raw_token=t))
        bump("keyword")

    return lines, ParsePreview(total_lines=len(lines), by_category=stats)
