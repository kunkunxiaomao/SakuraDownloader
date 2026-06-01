"""
Pixiv site plugin — wraps existing pixiv_app downloader helpers (no rewrite).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from pixiv_app.core.downloader import (
    download_binary,
    download_novel,
    fetch_illust_details,
    fetch_illust_pages,
    fetch_user_work_ids,
    parse_pixiv_target,
    search_illust_ids_by_keyword,
)
from pixiv_app.core.plugin.base import (
    BasePlugin,
    PluginParseError,
    Resource,
)

try:
    from fingerprint import default_profile
except Exception:  # pragma: no cover
    default_profile = None


_TAG_PATH_RE = re.compile(r"/tags/([^/]+)/?", re.IGNORECASE)


class PixivPlugin(BasePlugin):
    domain = "pixiv.net"
    name = "Pixiv"
    version = "2.0.0"

    def __init__(self, cookie: str = "", proxy_pool: Any = None) -> None:
        self.cookie = cookie
        self.proxy_pool = proxy_pool

    def set_auth(self, cookie: str) -> None:
        self.cookie = cookie or ""

    def set_proxy_pool(self, proxy_pool: Any) -> None:
        self.proxy_pool = proxy_pool

    def can_handle(self, url: str) -> bool:
        text = url.strip()
        if not text:
            return False
        lower = text.lower()
        if "pixiv.net" in lower or "pximg.net" in lower:
            return True
        return text.isdigit()

    def get_headers(self) -> dict[str, str]:
        from pixiv_app.core.downloader import build_headers

        headers = build_headers(cookie=self.cookie)
        if default_profile:
            headers.update(default_profile("pixiv").headers())
        return headers

    def parse(self, url: str) -> list[Resource]:
        raw = url.strip()
        if not raw:
            raise PluginParseError("Empty URL")

        if _TAG_PATH_RE.search(raw):
            m = _TAG_PATH_RE.search(raw)
            assert m is not None
            tag = unquote(m.group(1))
            return self._parse_tag(tag)

        try:
            kind, num_id = parse_pixiv_target(raw)
        except ValueError as exc:
            raise PluginParseError(str(exc)) from exc

        if kind == "novel":
            return [self._resource_novel_stub(num_id)]

        if kind == "user":
            return self._parse_user(num_id, input_kind="user")

        if kind == "illust":
            return [self._parse_illust(num_id)]

        if kind == "unknown":
            return [self._parse_illust(num_id)]

        raise PluginParseError(f"Unsupported Pixiv target kind: {kind}")

    def _parse_illust(self, illust_id: int) -> Resource:
        detail = fetch_illust_details(illust_id, cookie=self.cookie, proxy_pool=self.proxy_pool)
        page_urls = fetch_illust_pages(illust_id, cookie=self.cookie, proxy_pool=self.proxy_pool)
        files = [{"url": u, "page": i} for i, u in enumerate(page_urls) if u]
        thumb = page_urls[0] if page_urls else None
        return Resource(
            id=str(illust_id),
            url=f"https://www.pixiv.net/artworks/{illust_id}",
            title=str(detail.get("title") or ""),
            author=str(detail.get("userName") or ""),
            author_id=str(detail.get("userId") or ""),
            files=files,
            metadata={**detail, "kind": "illust", "illust_id": illust_id},
            thumbnail=thumb,
            created_at=str(detail.get("createDate") or detail.get("uploadDate") or "") or None,
        )

    def _parse_user(self, seed_id: int, *, input_kind: str) -> list[Resource]:
        resolved_uid, work_ids = fetch_user_work_ids(
            seed_id,
            cookie=self.cookie,
            proxy_pool=self.proxy_pool,
            input_kind=input_kind,
        )
        out: list[Resource] = []
        for wid in work_ids:
            out.append(
                Resource(
                    id=str(wid),
                    url=f"https://www.pixiv.net/artworks/{wid}",
                    title="",
                    author="",
                    author_id=str(resolved_uid),
                    files=[],
                    metadata={
                        "kind": "illust_lazy",
                        "illust_id": wid,
                        "resolved_user_id": resolved_uid,
                    },
                )
            )
        return out

    def _parse_tag(self, tag: str) -> list[Resource]:
        ids = search_illust_ids_by_keyword(
            tag,
            cookie=self.cookie,
            proxy_pool=self.proxy_pool,
            limit=50,
        )
        resources: list[Resource] = []
        for iid in ids:
            resources.append(
                Resource(
                    id=str(iid),
                    url=f"https://www.pixiv.net/artworks/{iid}",
                    title="",
                    author="",
                    author_id="",
                    files=[],
                    metadata={"kind": "illust_lazy", "illust_id": iid, "tag": tag},
                )
            )
        return resources

    def _resource_novel_stub(self, novel_id: int) -> Resource:
        return Resource(
            id=str(novel_id),
            url=f"https://www.pixiv.net/novel/show.php?id={novel_id}",
            title="",
            author="",
            author_id="",
            files=[],
            metadata={"kind": "novel", "novel_id": novel_id},
        )

    def download(self, resource: Resource, save_path: Path) -> list[Path]:
        save_path.mkdir(parents=True, exist_ok=True)
        kind = resource.metadata.get("kind")

        if kind == "novel":
            novel_id = int(resource.metadata.get("novel_id") or resource.id)
            download_novel(
                novel_id,
                cookie=self.cookie,
                save_root=save_path,
                save_subdir="novels",
                proxy_pool=self.proxy_pool,
                library_db_path=None,
            )
            matches = list(save_path.rglob(f"*{novel_id}.txt"))
            return matches

        illust_id = int(resource.metadata.get("illust_id") or resource.id)

        if kind == "illust_lazy" or not resource.files:
            detail = fetch_illust_details(illust_id, cookie=self.cookie, proxy_pool=self.proxy_pool)
            page_urls = fetch_illust_pages(illust_id, cookie=self.cookie, proxy_pool=self.proxy_pool)
            resource.files = [{"url": u, "page": i} for i, u in enumerate(page_urls) if u]
            resource.metadata.update(detail)

        downloaded: list[Path] = []
        subdir = resource.metadata.get("save_subdir")
        target_dir = save_path / subdir if subdir else save_path
        target_dir.mkdir(parents=True, exist_ok=True)

        for item in resource.files:
            file_url = item.get("url")
            if not file_url:
                continue
            ok, _msg = download_binary(
                file_url,
                save_dir=target_dir,
                cookie=self.cookie,
                proxy_pool=self.proxy_pool,
            )
            if ok:
                fname = file_url.rsplit("/", 1)[-1].split("?")[0]
                downloaded.append(target_dir / fname)

        return downloaded


plugin_class = PixivPlugin
