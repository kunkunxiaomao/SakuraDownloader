from __future__ import annotations

import re
from typing import Literal

from pixiv_app.core.downloader import parse_pixiv_target
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

    ui_mode matches GUI combo selection so validation stays aligned with existing rules.
    """
    tokens = split_input_blob(raw)
    lines: list[ParsedLine] = []
    stats: dict[str, int] = {}

    def bump(key: str) -> None:
        stats[key] = stats.get(key, 0) + 1

    if ui_mode == "keyword":
        kw = " ".join(tokens) if tokens else raw.strip()
        if kw.startswith("#"):
            kw = kw[1:].strip()
        if kw:
            lines.append(ParsedLine(category="keyword", id_value=0, keyword_text=kw, raw_token=raw.strip()))
            bump("keyword")
        return lines, ParsePreview(total_lines=len(lines), by_category=stats)

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

        try:
            kind, num_id = parse_pixiv_target(t)
        except ValueError:
            continue

        if ui_mode == "novel":
            if kind == "novel":
                lines.append(ParsedLine(category="novel", id_value=num_id, raw_token=t, pixiv_kind=kind))
                bump("novel")
            elif kind == "unknown":
                lines.append(ParsedLine(category="novel", id_value=num_id, raw_token=t, pixiv_kind=kind))
                bump("novel")
            continue

        if ui_mode == "illust":
            if kind == "illust":
                lines.append(ParsedLine(category="illust", id_value=num_id, raw_token=t, pixiv_kind=kind))
                bump("illust")
            elif kind == "unknown":
                lines.append(ParsedLine(category="illust", id_value=num_id, raw_token=t, pixiv_kind=kind))
                bump("illust")
            continue

        # ui_mode == "user"
        if kind == "user":
            lines.append(ParsedLine(category="user", id_value=num_id, raw_token=t, pixiv_kind=kind))
            bump("user")
        elif kind == "illust":
            lines.append(ParsedLine(category="user", id_value=num_id, raw_token=t, pixiv_kind=kind))
            bump("user")
        elif kind == "unknown":
            lines.append(ParsedLine(category="user", id_value=num_id, raw_token=t, pixiv_kind=kind))
            bump("user")

    return lines, ParsePreview(total_lines=len(lines), by_category=stats)
