from __future__ import annotations

import json
from pathlib import Path
from typing import Any


Cookie = dict[str, Any]


def parse_cookie_text(text: str) -> list[Cookie]:
    """Parse JSON cookie exports or Netscape cookies.txt content."""
    text = text.strip()
    if not text:
        raise ValueError("Cookie 文件为空。")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None

    if payload is not None:
        raw_items = payload.get("cookies", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            raise ValueError("Cookie JSON 必须是数组，或包含 cookies 数组。")
        cookies = [_normalize_json_cookie(item) for item in raw_items if isinstance(item, dict)]
    else:
        cookies = _parse_netscape_cookie_text(text)

    cookies = [item for item in cookies if item.get("name") and item.get("value") is not None]
    if not cookies:
        raise ValueError("没有解析到有效 Cookie。")
    return cookies


def filter_cookies_for_domains(cookies: list[Cookie], domains: tuple[str, ...]) -> list[Cookie]:
    result: list[Cookie] = []
    for cookie in cookies:
        domain = str(cookie.get("domain", "")).lower().lstrip(".")
        if any(domain == item or domain.endswith("." + item) for item in domains):
            result.append(cookie)
    return result


def cookie_domains(cookies: list[Cookie]) -> list[str]:
    domains = sorted({str(item.get("domain", "")).lower().lstrip(".") for item in cookies if item.get("domain")})
    return [item for item in domains if item]


def has_cookie_domain(cookies: list[Cookie], domains: tuple[str, ...]) -> bool:
    return bool(filter_cookies_for_domains(cookies, domains))


def cookies_to_header(cookies: list[Cookie], domains: tuple[str, ...] | None = None) -> str:
    selected = filter_cookies_for_domains(cookies, domains) if domains else cookies
    pairs: list[str] = []
    seen: set[str] = set()
    for cookie in selected:
        name = str(cookie.get("name", "")).strip()
        value = str(cookie.get("value", ""))
        if not name or name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={value}")
    if not pairs:
        raise ValueError("没有找到匹配站点的 Cookie。")
    return "; ".join(pairs)


def cookies_to_playwright(cookies: list[Cookie], domains: tuple[str, ...] | None = None) -> list[Cookie]:
    selected = filter_cookies_for_domains(cookies, domains) if domains else cookies
    output: list[Cookie] = []
    for cookie in selected:
        item: Cookie = {
            "name": str(cookie["name"]),
            "value": str(cookie.get("value", "")),
            "domain": str(cookie.get("domain") or ""),
            "path": str(cookie.get("path") or "/"),
            "secure": bool(cookie.get("secure", False)),
            "httpOnly": bool(cookie.get("httpOnly", False)),
        }
        expires = cookie.get("expires")
        if expires is not None:
            try:
                item["expires"] = int(float(expires))
            except (TypeError, ValueError):
                pass
        same_site = str(cookie.get("sameSite", "") or "").strip()
        if same_site in {"Strict", "Lax", "None"}:
            item["sameSite"] = same_site
        output.append(item)
    if not output:
        raise ValueError("没有找到匹配站点的 Cookie。")
    return output


def save_playwright_cookies(cookies: list[Cookie], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def cookie_summary(cookies: list[Cookie]) -> str:
    domains = sorted({str(item.get("domain", "")).lstrip(".") or "-" for item in cookies})
    shown = ", ".join(domains[:5])
    suffix = "..." if len(domains) > 5 else ""
    return f"{len(cookies)} 个 Cookie；域名：{shown}{suffix}"


def _parse_netscape_cookie_text(text: str) -> list[Cookie]:
    cookies: list[Cookie] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#HttpOnly_"):
            http_only = line.startswith("#HttpOnly_")
            line = line.removeprefix("#HttpOnly_")
        elif line.startswith("#"):
            continue
        else:
            http_only = False

        parts = line.split("\t")
        if len(parts) < 7:
            parts = line.split()
        if len(parts) < 7:
            continue

        domain, _include_subdomains, path, secure, expires, name = parts[:6]
        value = "\t".join(parts[6:])
        cookies.append(
            {
                "domain": domain,
                "path": path or "/",
                "secure": secure.upper() == "TRUE",
                "expires": int(expires) if str(expires).isdigit() else -1,
                "name": name,
                "value": value,
                "httpOnly": http_only,
                "sameSite": "Lax",
            }
        )
    return cookies


def _normalize_json_cookie(item: Cookie) -> Cookie:
    expires = item.get("expires", item.get("expirationDate", item.get("expiry")))
    same_site = _normalize_same_site(item.get("sameSite"))
    return {
        "domain": str(item.get("domain") or item.get("host") or ""),
        "path": str(item.get("path") or "/"),
        "secure": bool(item.get("secure", False)),
        "expires": expires,
        "name": str(item.get("name") or ""),
        "value": str(item.get("value") or ""),
        "httpOnly": bool(item.get("httpOnly", item.get("httponly", False))),
        "sameSite": same_site,
    }


def _normalize_same_site(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"strict", "samesitestrict"}:
        return "Strict"
    if text in {"none", "no_restriction", "samesitenone"}:
        return "None"
    return "Lax"
