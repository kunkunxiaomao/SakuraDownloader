from __future__ import annotations

import concurrent.futures
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, urlparse

import requests

from pixiv_app.core.library import DEFAULT_LIBRARY_DB, DownloadFileRecord, PixivLibrary
from pixiv_app.core.proxy_pool import ProxyPool


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30


@dataclass
class DownloadResult:
    work_id: int
    total_pages: int
    downloaded_pages: int
    skipped_pages: int
    failed_pages: int
    ok: bool
    message: str = ""


class PixivRequestError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def sanitize_filename(name: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", str(name)).strip().rstrip(".")
    return cleaned or fallback


def build_headers(cookie: str = "", referer: Optional[str] = None) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://www.pixiv.net",
    }
    headers["Referer"] = referer or "https://www.pixiv.net/"
    if cookie:
        headers["Cookie"] = cookie
    return headers


def create_session(cookie: str = "") -> requests.Session:
    session = requests.Session()
    session.headers.update(build_headers(cookie=cookie))
    return session


def ensure_success(response: requests.Response) -> None:
    response.raise_for_status()


def parse_pixiv_target(raw_value: str) -> tuple[str, int]:
    text = raw_value.strip()
    if not text:
        raise ValueError("请输入 Pixiv 作者 ID、作品 ID、小说 ID，或对应链接。")

    patterns = (
        (r"/users/(\d+)", "user"),
        (r"/artworks/(\d+)", "illust"),
        (r"[?&]illust_id=(\d+)", "illust"),
        (r"/novel/show\.php\?id=(\d+)", "novel"),
        (r"/novel/show\.php.*?[?&]id=(\d+)", "novel"),
        (r"/novel/(\d+)", "novel"),
    )
    for pattern, target_kind in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return target_kind, int(match.group(1))

    if text.isdigit():
        return "unknown", int(text)

    raise ValueError("无法识别输入内容，请填写 Pixiv 作者 ID、作品 ID、小说 ID，或完整链接。")


def extract_work_ids(payload: dict) -> list[int]:
    body = payload.get("body", {})
    work_ids: set[int] = set()
    for section in ("illusts", "manga"):
        section_data = body.get(section, {})
        if not isinstance(section_data, dict):
            continue
        for key in section_data.keys():
            if str(key).isdigit():
                work_ids.add(int(key))
    return sorted(work_ids, reverse=True)


def _pick_proxy(proxy_pool: Optional[ProxyPool]) -> tuple[Optional[dict[str, str]], object]:
    if not proxy_pool:
        return None, None
    proxy = proxy_pool.get_proxy(rotate=True)
    if not proxy:
        return None, None
    return {"http": proxy.proxy_url, "https": proxy.proxy_url}, proxy


def request_json(
    session: requests.Session,
    url: str,
    *,
    cookie: str = "",
    referer: Optional[str] = None,
    proxy_pool: Optional[ProxyPool] = None,
    context: str = "Pixiv 请求",
) -> dict:
    last_error: Optional[Exception] = None
    for _ in range(3):
        proxies, proxy_obj = _pick_proxy(proxy_pool)
        try:
            response = session.get(
                url,
                headers=build_headers(cookie=cookie, referer=referer),
                timeout=DEFAULT_TIMEOUT,
                proxies=proxies,
            )
            if response.status_code == 404:
                raise PixivRequestError(f"{context}不存在，或当前账号无权访问。", status_code=404)
            ensure_success(response)
            payload = response.json()
            if isinstance(payload, dict) and payload.get("error"):
                message = str(payload.get("message") or "返回了错误响应")
                raise PixivRequestError(f"{context}失败: {message}", status_code=response.status_code)
            if not isinstance(payload, dict):
                raise PixivRequestError(f"{context}返回格式异常。", status_code=response.status_code)
            return payload
        except PixivRequestError as exc:
            last_error = exc
            if proxy_pool and proxy_obj:
                proxy_pool.mark_failed(proxy_obj)
            if exc.status_code == 404:
                break
            time.sleep(0.5)
        except Exception as exc:
            last_error = exc
            if proxy_pool and proxy_obj:
                proxy_pool.mark_failed(proxy_obj)
            time.sleep(0.5)

    if last_error:
        if isinstance(last_error, PixivRequestError):
            raise last_error
        raise PixivRequestError(f"{context}失败: {last_error}")
    raise PixivRequestError(f"{context}失败。")


def fetch_illust_details(
    illust_id: int,
    *,
    cookie: str = "",
    session: Optional[requests.Session] = None,
    proxy_pool: Optional[ProxyPool] = None,
) -> dict:
    close_session = session is None
    session = session or create_session(cookie)
    try:
        payload = request_json(
            session,
            f"https://www.pixiv.net/ajax/illust/{illust_id}?lang=zh",
            cookie=cookie,
            referer=f"https://www.pixiv.net/artworks/{illust_id}",
            proxy_pool=proxy_pool,
            context=f"作品 {illust_id} 信息请求",
        )
        return payload.get("body", {}) if isinstance(payload.get("body"), dict) else {}
    finally:
        if close_session:
            session.close()


def resolve_user_id_from_illust(
    illust_id: int,
    *,
    cookie: str = "",
    session: Optional[requests.Session] = None,
    proxy_pool: Optional[ProxyPool] = None,
) -> int:
    body = fetch_illust_details(
        illust_id,
        cookie=cookie,
        session=session,
        proxy_pool=proxy_pool,
    )
    user_id = body.get("userId")
    if str(user_id).isdigit():
        return int(user_id)
    raise PixivRequestError(f"作品 {illust_id} 未返回有效作者 ID。")


def fetch_user_work_ids(
    user_id: int,
    cookie: str = "",
    session: Optional[requests.Session] = None,
    proxy_pool: Optional[ProxyPool] = None,
    input_kind: str = "user",
) -> tuple[int, list[int]]:
    close_session = session is None
    session = session or create_session(cookie)
    try:
        def load_user_works(resolved_user_id: int) -> list[int]:
            payload = request_json(
                session,
                f"https://www.pixiv.net/ajax/user/{resolved_user_id}/profile/all?sensitiveFilterMode=userSetting&lang=zh",
                cookie=cookie,
                referer=f"https://www.pixiv.net/users/{resolved_user_id}",
                proxy_pool=proxy_pool,
                context=f"作者 {resolved_user_id} 作品列表请求",
            )
            return extract_work_ids(payload)

        if input_kind == "illust":
            resolved_user_id = resolve_user_id_from_illust(
                user_id,
                cookie=cookie,
                session=session,
                proxy_pool=proxy_pool,
            )
            return resolved_user_id, load_user_works(resolved_user_id)

        try:
            return user_id, load_user_works(user_id)
        except PixivRequestError as exc:
            if input_kind != "unknown" or exc.status_code != 404:
                raise

        try:
            resolved_user_id = resolve_user_id_from_illust(
                user_id,
                cookie=cookie,
                session=session,
                proxy_pool=proxy_pool,
            )
            return resolved_user_id, load_user_works(resolved_user_id)
        except PixivRequestError as exc:
            if exc.status_code == 404:
                raise PixivRequestError("输入的 ID 既不是可访问的作者，也不是可访问的作品。", status_code=404) from exc
            raise
    finally:
        if close_session:
            session.close()


def fetch_illust_pages(
    illust_id: int,
    cookie: str = "",
    session: Optional[requests.Session] = None,
    proxy_pool: Optional[ProxyPool] = None,
) -> list[str]:
    close_session = session is None
    session = session or create_session(cookie)
    referer = f"https://www.pixiv.net/artworks/{illust_id}"
    try:
        payload = request_json(
            session,
            f"https://www.pixiv.net/ajax/illust/{illust_id}/pages?lang=zh",
            cookie=cookie,
            referer=referer,
            proxy_pool=proxy_pool,
            context=f"作品 {illust_id} 页面请求",
        )
        body = payload.get("body", [])
        if isinstance(body, list):
            urls = [page.get("urls", {}).get("original") for page in body if isinstance(page, dict)]
            urls = [url for url in urls if url]
            if urls:
                return urls

        detail = fetch_illust_details(
            illust_id,
            cookie=cookie,
            session=session,
            proxy_pool=proxy_pool,
        )
        original_url = detail.get("urls", {}).get("original") if isinstance(detail.get("urls"), dict) else None
        return [original_url] if original_url else []
    finally:
        if close_session:
            session.close()


def fetch_novel_details(
    novel_id: int,
    *,
    cookie: str = "",
    session: Optional[requests.Session] = None,
    proxy_pool: Optional[ProxyPool] = None,
) -> dict:
    close_session = session is None
    session = session or create_session(cookie)
    try:
        payload = request_json(
            session,
            f"https://www.pixiv.net/ajax/novel/{novel_id}?lang=zh",
            cookie=cookie,
            referer=f"https://www.pixiv.net/novel/show.php?id={novel_id}",
            proxy_pool=proxy_pool,
            context=f"小说 {novel_id} 信息请求",
        )
        return payload.get("body", {}) if isinstance(payload.get("body"), dict) else {}
    finally:
        if close_session:
            session.close()


def fetch_novel_text(
    novel_id: int,
    *,
    cookie: str = "",
    session: Optional[requests.Session] = None,
    proxy_pool: Optional[ProxyPool] = None,
) -> tuple[dict, str]:
    close_session = session is None
    session = session or create_session(cookie)
    try:
        detail = fetch_novel_details(
            novel_id,
            cookie=cookie,
            session=session,
            proxy_pool=proxy_pool,
        )
        content = detail.get("content")
        if isinstance(content, str) and content.strip():
            return detail, content

        referer = f"https://www.pixiv.net/novel/show.php?id={novel_id}"
        content_body: Optional[dict] = None
        for url in (
            f"https://www.pixiv.net/ajax/novel/text/{novel_id}?lang=zh",
            f"https://www.pixiv.net/ajax/novel/{novel_id}/text?lang=zh",
        ):
            try:
                payload = request_json(
                    session,
                    url,
                    cookie=cookie,
                    referer=referer,
                    proxy_pool=proxy_pool,
                    context=f"小说 {novel_id} 正文请求",
                )
                body = payload.get("body", {})
                if isinstance(body, dict):
                    content_body = body
                    break
            except PixivRequestError as exc:
                if exc.status_code != 404:
                    raise
        if content_body is None:
            raise PixivRequestError(f"未获取到小说 {novel_id} 正文。")

        content = content_body.get("content")
        if not isinstance(content, str) or not content.strip():
            raise PixivRequestError(f"小说 {novel_id} 正文为空。")
        return detail, content
    finally:
        if close_session:
            session.close()


def search_illust_ids_by_keyword(
    keyword: str,
    *,
    cookie: str = "",
    session: Optional[requests.Session] = None,
    proxy_pool: Optional[ProxyPool] = None,
    limit: int = 30,
) -> list[int]:
    cleaned_keyword = keyword.strip()
    if not cleaned_keyword:
        raise ValueError("关键词不能为空。")

    close_session = session is None
    session = session or create_session(cookie)
    encoded = quote(cleaned_keyword, safe="")
    work_ids: list[int] = []
    seen: set[int] = set()
    page = 1
    try:
        while len(work_ids) < limit and page <= max(3, limit):
            payload = request_json(
                session,
                (
                    f"https://www.pixiv.net/ajax/search/artworks/{encoded}"
                    f"?word={encoded}&order=date_d&mode=all&p={page}&s_mode=s_tag_full&type=all&lang=zh"
                ),
                cookie=cookie,
                referer=f"https://www.pixiv.net/tags/{encoded}/artworks",
                proxy_pool=proxy_pool,
                context=f"关键词“{cleaned_keyword}”搜索请求",
            )
            body = payload.get("body", {})
            illust_manga = body.get("illustManga", {}) if isinstance(body, dict) else {}
            items = illust_manga.get("data", []) if isinstance(illust_manga, dict) else []
            if not isinstance(items, list) or not items:
                break

            added_this_page = 0
            for item in items:
                item_id = item.get("id") if isinstance(item, dict) else None
                if str(item_id).isdigit():
                    illust_id = int(item_id)
                    if illust_id not in seen:
                        seen.add(illust_id)
                        work_ids.append(illust_id)
                        added_this_page += 1
                        if len(work_ids) >= limit:
                            break
            if added_this_page == 0:
                break
            page += 1
        return work_ids[:limit]
    finally:
        if close_session:
            session.close()


def is_file_complete(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 1024


def download_binary(
    file_url: str,
    save_dir: Path,
    cookie: str = "",
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_TIMEOUT,
    retry_count: int = 3,
    proxy_pool: Optional[ProxyPool] = None,
) -> tuple[bool, str]:
    close_session = session is None
    session = session or create_session(cookie)
    save_dir.mkdir(parents=True, exist_ok=True)

    filename = os.path.basename(urlparse(file_url).path)
    save_path = save_dir / filename
    if is_file_complete(save_path):
        return True, f"跳过已存在文件: {filename}"
    temp_path = save_path.with_suffix(save_path.suffix + ".part")

    try:
        for attempt in range(1, retry_count + 1):
            proxies, proxy_obj = _pick_proxy(proxy_pool)
            try:
                response = session.get(
                    file_url,
                    headers=build_headers(cookie=cookie, referer="https://www.pixiv.net/"),
                    timeout=timeout,
                    stream=True,
                    proxies=proxies,
                )
                ensure_success(response)
                with open(temp_path, "wb") as file_handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            file_handle.write(chunk)
                if temp_path.stat().st_size <= 1024:
                    raise IOError("文件过小，可能不是有效图片。")
                temp_path.replace(save_path)
                return True, f"下载成功: {filename}"
            except Exception as exc:
                if proxy_pool and proxy_obj:
                    proxy_pool.mark_failed(proxy_obj)
                if temp_path.exists():
                    temp_path.unlink()
                if attempt == retry_count:
                    return False, f"下载失败 {filename}: {exc}"
                time.sleep(min(0.8 * attempt, 2.0))
    finally:
        if close_session:
            session.close()


def save_text_file(save_path: Path, content: str) -> tuple[bool, str]:
    save_path.parent.mkdir(parents=True, exist_ok=True)
    if save_path.exists() and save_path.stat().st_size > 32:
        return True, f"跳过已存在文件: {save_path.name}"
    temp_path = save_path.with_suffix(save_path.suffix + ".part")
    with open(temp_path, "w", encoding="utf-8-sig") as file_handle:
        file_handle.write(content)
    temp_path.replace(save_path)
    return True, f"保存成功: {save_path.name}"


def download_illust(
    illust_id: int,
    user_id: int,
    cookie: str = "",
    save_root: str | Path = "Sakura_Downloads",
    save_subdir: Optional[str] = None,
    request_delay: float = 0.0,
    stop_event: Optional[threading.Event] = None,
    proxy_pool: Optional[ProxyPool] = None,
    library_db_path: str | Path | None = DEFAULT_LIBRARY_DB,
) -> DownloadResult:
    if stop_event and stop_event.is_set():
        return DownloadResult(illust_id, 0, 0, 0, 0, False, "任务已停止")
    if request_delay > 0:
        time.sleep(request_delay)

    session = create_session(cookie)
    subdir = save_subdir or str(user_id)
    save_dir = Path(save_root) / subdir
    library: PixivLibrary | None = PixivLibrary(library_db_path) if library_db_path else None
    file_records: list[DownloadFileRecord] = []
    try:
        detail = fetch_illust_details(illust_id, cookie=cookie, session=session, proxy_pool=proxy_pool)
        if library is not None:
            library.ensure_artwork_stub(illust_id, artwork_type="illust")
            library.upsert_artwork(detail, artwork_type="illust")
        resolved_user_id = detail.get("userId")
        if user_id == 0 and str(resolved_user_id).isdigit():
            user_id = int(resolved_user_id)
        page_urls = fetch_illust_pages(illust_id, cookie=cookie, session=session, proxy_pool=proxy_pool)
        if not page_urls:
            result = DownloadResult(illust_id, 0, 0, 0, 1, False, "未获取到原图地址")
            if library is not None:
                library.ensure_artwork_stub(illust_id, artwork_type="illust")
                library.record_download_history(
                    artwork_id=illust_id,
                    status="failed",
                    downloaded_pages=0,
                    skipped_pages=0,
                    failed_pages=1,
                    message=result.message,
                )
            return result

        downloaded_pages, skipped_pages, failed_pages = 0, 0, 0
        for page_index, page_url in enumerate(page_urls):
            if stop_event and stop_event.is_set():
                break
            filename = os.path.basename(urlparse(page_url).path)
            target_path = save_dir / filename
            if is_file_complete(target_path):
                skipped_pages += 1
                file_records.append(
                    DownloadFileRecord(
                        page_index=page_index,
                        file_path=str(target_path.resolve()),
                        source_url=page_url,
                        status="skipped",
                        size_bytes=target_path.stat().st_size,
                    )
                )
                continue
            success, _ = download_binary(
                page_url,
                save_dir=save_dir,
                cookie=cookie,
                session=session,
                proxy_pool=proxy_pool,
            )
            if success:
                downloaded_pages += 1
                file_records.append(
                    DownloadFileRecord(
                        page_index=page_index,
                        file_path=str(target_path.resolve()),
                        source_url=page_url,
                        status="downloaded",
                        size_bytes=target_path.stat().st_size if target_path.exists() else 0,
                    )
                )
            else:
                failed_pages += 1
                file_records.append(
                    DownloadFileRecord(
                        page_index=page_index,
                        file_path=str(target_path.resolve()),
                        source_url=page_url,
                        status="failed",
                    )
                )

        total_pages = len(page_urls)
        ok = failed_pages == 0 and total_pages > 0
        message = f"作品 {illust_id}: 共 {total_pages} 页, 新下 {downloaded_pages}, 跳过 {skipped_pages}, 失败 {failed_pages}"
        if library is not None:
            library.ensure_artwork_stub(illust_id, artwork_type="illust")
            library.record_files(illust_id, file_records)
            library.record_download_history(
                artwork_id=illust_id,
                status="success" if ok else "failed",
                downloaded_pages=downloaded_pages,
                skipped_pages=skipped_pages,
                failed_pages=failed_pages,
                message=message,
            )
        return DownloadResult(illust_id, total_pages, downloaded_pages, skipped_pages, failed_pages, ok, message)
    except Exception as exc:
        message = f"作品 {illust_id} 处理失败: {exc}"
        if library is not None:
            library.ensure_artwork_stub(illust_id, artwork_type="illust")
            library.record_download_history(
                artwork_id=illust_id,
                status="failed",
                downloaded_pages=0,
                skipped_pages=0,
                failed_pages=1,
                message=message,
            )
        return DownloadResult(illust_id, 0, 0, 0, 1, False, message)
    finally:
        if library is not None:
            library.close()
        session.close()


def download_novel(
    novel_id: int,
    cookie: str = "",
    save_root: str | Path = "Sakura_Downloads",
    save_subdir: str = "novels",
    request_delay: float = 0.0,
    stop_event: Optional[threading.Event] = None,
    proxy_pool: Optional[ProxyPool] = None,
    library_db_path: str | Path | None = DEFAULT_LIBRARY_DB,
) -> DownloadResult:
    if stop_event and stop_event.is_set():
        return DownloadResult(novel_id, 0, 0, 0, 0, False, "任务已停止")
    if request_delay > 0:
        time.sleep(request_delay)

    session = create_session(cookie)
    library: PixivLibrary | None = PixivLibrary(library_db_path) if library_db_path else None
    library_artwork_id = novel_id
    try:
        detail, content = fetch_novel_text(
            novel_id,
            cookie=cookie,
            session=session,
            proxy_pool=proxy_pool,
        )
        detail.setdefault("id", novel_id)
        detail.setdefault("novelId", novel_id)
        if library is not None:
            library_artwork_id = library.ensure_artwork_stub(novel_id, artwork_type="novel")
            library.upsert_artwork(detail, artwork_type="novel")
        title = sanitize_filename(detail.get("title", ""), f"novel_{novel_id}")
        user_name = sanitize_filename(detail.get("userName", ""), "unknown_author")
        user_id = detail.get("userId")
        author_dir = f"{user_name}_{user_id}" if str(user_id).isdigit() else user_name
        save_dir = Path(save_root) / save_subdir / sanitize_filename(author_dir, f"novel_{novel_id}")
        save_path = save_dir / f"{title}_{novel_id}.txt"
        text = "\n".join(
            [
                f"标题: {detail.get('title', title)}",
                f"作者: {detail.get('userName', '')}",
                f"小说ID: {novel_id}",
                "",
                content,
            ]
        )
        _, message = save_text_file(save_path, text)
        result_message = f"小说 {novel_id}: {message}"
        status = "skipped" if "跳过" in message else "downloaded"
        downloaded_pages = 1 if status == "downloaded" else 0
        skipped_pages = 1 if status == "skipped" else 0
        if library is not None:
            library.record_files(
                library_artwork_id,
                [
                    DownloadFileRecord(
                        page_index=0,
                        file_path=str(save_path.resolve()),
                        source_url=f"https://www.pixiv.net/novel/show.php?id={novel_id}",
                        status=status,
                        size_bytes=save_path.stat().st_size if save_path.exists() else 0,
                    )
                ],
            )
            library.record_download_history(
                artwork_id=library_artwork_id,
                status="success",
                downloaded_pages=downloaded_pages,
                skipped_pages=skipped_pages,
                failed_pages=0,
                message=result_message,
            )
        return DownloadResult(novel_id, 1, downloaded_pages, skipped_pages, 0, True, result_message)
    except Exception as exc:
        message = f"小说 {novel_id} 下载失败: {exc}"
        if library is not None:
            library_artwork_id = library.ensure_artwork_stub(novel_id, artwork_type="novel")
            library.record_download_history(
                artwork_id=library_artwork_id,
                status="failed",
                downloaded_pages=0,
                skipped_pages=0,
                failed_pages=1,
                message=message,
            )
        return DownloadResult(novel_id, 1, 0, 0, 1, False, message)
    finally:
        if library is not None:
            library.close()
        session.close()


def resolve_workers(user_workers: int, proxy_mode: bool) -> int:
    if not proxy_mode:
        return max(1, user_workers)
    return max(3, min(user_workers, 6))


def _download_illust_batch(
    work_ids: list[int],
    *,
    cookie: str = "",
    max_workers: int = 6,
    request_delay: float = 0.0,
    save_root: str | Path = "Sakura_Downloads",
    save_subdir: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[DownloadResult, int, int], None]] = None,
    proxy_pool: Optional[ProxyPool] = None,
    user_id_hint: int = 0,
) -> list[DownloadResult]:
    worker_count = resolve_workers(max_workers, proxy_mode=proxy_pool is not None)
    results: list[DownloadResult] = []
    if not work_ids:
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                download_illust,
                illust_id=work_id,
                user_id=user_id_hint,
                cookie=cookie,
                save_root=save_root,
                save_subdir=save_subdir,
                request_delay=request_delay,
                stop_event=stop_event,
                proxy_pool=proxy_pool,
            ): work_id
            for work_id in work_ids
        }
        completed, total = 0, len(futures)
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            completed += 1
            if progress_callback:
                progress_callback(result, completed, total)
            if stop_event and stop_event.is_set():
                break

    results.sort(key=lambda item: item.work_id, reverse=True)
    return results


def download_user_works(
    user_id: int,
    cookie: str = "",
    max_workers: int = 6,
    request_delay: float = 0.0,
    save_root: str | Path = "Sakura_Downloads",
    stop_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[DownloadResult, int, int], None]] = None,
    work_ids: Optional[list[int]] = None,
    proxy_pool: Optional[ProxyPool] = None,
) -> tuple[list[int], list[DownloadResult]]:
    if work_ids is None:
        _, work_ids = fetch_user_work_ids(user_id=user_id, cookie=cookie, proxy_pool=proxy_pool)
    results = _download_illust_batch(
        work_ids,
        cookie=cookie,
        max_workers=max_workers,
        request_delay=request_delay,
        save_root=save_root,
        save_subdir=str(user_id),
        stop_event=stop_event,
        progress_callback=progress_callback,
        proxy_pool=proxy_pool,
        user_id_hint=user_id,
    )
    return work_ids, results


def download_keyword_works(
    keyword: str,
    *,
    cookie: str = "",
    max_workers: int = 6,
    request_delay: float = 0.0,
    save_root: str | Path = "Sakura_Downloads",
    stop_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[DownloadResult, int, int], None]] = None,
    proxy_pool: Optional[ProxyPool] = None,
    limit: int = 30,
    work_ids: Optional[list[int]] = None,
) -> tuple[list[int], list[DownloadResult]]:
    if work_ids is None:
        work_ids = search_illust_ids_by_keyword(
            keyword,
            cookie=cookie,
            proxy_pool=proxy_pool,
            limit=limit,
        )
    results = _download_illust_batch(
        work_ids,
        cookie=cookie,
        max_workers=max_workers,
        request_delay=request_delay,
        save_root=save_root,
        save_subdir=str(Path("keywords") / sanitize_filename(keyword, "keyword")),
        stop_event=stop_event,
        progress_callback=progress_callback,
        proxy_pool=proxy_pool,
    )
    return work_ids, results
