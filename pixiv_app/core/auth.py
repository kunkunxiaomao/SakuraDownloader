from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from pixiv_app.core.downloader import USER_AGENT, build_headers


LOGIN_PAGE_URL = "https://accounts.pixiv.net/login"
LOGIN_AJAX_URL = "https://accounts.pixiv.net/ajax/login?lang=zh"
VALIDATE_URL = "https://www.pixiv.net/setting_user.php"


@dataclass
class AuthResult:
    success: bool
    message: str
    cookie: str = ""
    requires_verification: bool = False
    user_name: str = ""
    user_id: str = ""


class SessionStore:
    def __init__(self, path: str | Path = "pixiv_app_session.json") -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class PixivAuthClient:
    def __init__(self) -> None:
        self.timeout = 20

    def validate_cookie(self, cookie: str) -> AuthResult:
        cookie = cookie.strip()
        if not cookie:
            return AuthResult(False, "Cookie 为空，请先填写或登录。")

        session = requests.Session()
        session.headers.update(build_headers(cookie=cookie, referer="https://www.pixiv.net/"))
        try:
            response = session.get(VALIDATE_URL, allow_redirects=True, timeout=self.timeout)
            final_url = response.url
            if "return_to=" in final_url or "accounts.pixiv.net" in final_url:
                return AuthResult(False, "当前 Cookie 已失效或未登录，请更新会话。")
            user_id, user_name = self._extract_user_info(response.text)
            return AuthResult(True, "Cookie 有效，可直接开始抓取。", cookie=cookie, user_name=user_name, user_id=user_id)
        except Exception as exc:
            return AuthResult(False, f"Cookie 检测失败: {exc}")

    def login_with_password(self, login_id: str, password: str) -> AuthResult:
        login_id = login_id.strip()
        password = password.strip()
        if not login_id or not password:
            return AuthResult(False, "账号和密码都不能为空。")

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Origin": "https://accounts.pixiv.net",
                "Referer": LOGIN_PAGE_URL,
                "X-Requested-With": "XMLHttpRequest",
            }
        )

        try:
            login_page = session.get(LOGIN_PAGE_URL, timeout=self.timeout)
            login_page.raise_for_status()
            tt = self._extract_tt(login_page.text)

            payload = {
                "login_id": login_id,
                "password": password,
                "source": "accounts",
                "app_ios": "0",
                "ref": "",
                "return_to": "https://www.pixiv.net/",
                "g_recaptcha_response": "",
                "recaptcha_enterprise_score_token": "",
            }
            headers = dict(session.headers)
            if tt:
                headers["X-CSRF-Token"] = tt

            response = session.post(
                LOGIN_AJAX_URL,
                data=urlencode(payload),
                headers={**headers, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()

            if isinstance(body, dict) and body.get("body", {}).get("success"):
                cookie = self._cookies_to_header(session.cookies)
                validation = self.validate_cookie(cookie)
                if validation.success:
                    validation.message = "账号密码登录成功，会话已可用。"
                    return validation
                return AuthResult(True, "登录接口返回成功，但会话校验未完全通过，请手动检测。", cookie=cookie)

            body_payload = body.get("body", {}) if isinstance(body, dict) else {}
            errors = body_payload.get("errors", {}) if isinstance(body_payload, dict) else {}
            if errors.get("recaptcha") or body_payload.get("requireExtraVerification") or body_payload.get("requireTwoFactorAuthentication"):
                return AuthResult(
                    False,
                    "Pixiv 要求额外验证或二步验证，程序无法静默完成。请改用 Cookie 登录，或先在浏览器登录后导出 Cookie。",
                    requires_verification=True,
                )
            message = self._flatten_error_message(errors) or "账号密码登录失败，请检查账号、密码或改用 Cookie。"
            return AuthResult(False, message)
        except Exception as exc:
            return AuthResult(False, f"账号密码登录失败: {exc}")

    def _extract_tt(self, html_text: str) -> str:
        match = re.search(r'"pixivAccount\.tt":"([^"]+)"', html_text)
        return match.group(1) if match else ""

    def _extract_user_info(self, html_text: str) -> tuple[str, str]:
        user_id = ""
        user_name = ""
        patterns = [
            r'"userData":\{"id":"(\d+)","pixivId":"[^"]*","name":"([^"]+)"',
            r'"user_id":"(\d+)".*?"name":"([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text)
            if match:
                user_id = match.group(1)
                user_name = html.unescape(match.group(2))
                break
        return user_id, user_name

    def _flatten_error_message(self, errors: dict[str, Any]) -> str:
        messages: list[str] = []
        for key, value in errors.items():
            if isinstance(value, str) and value.strip():
                messages.append(f"{key}: {value}")
            elif isinstance(value, list):
                text_items = [str(item).strip() for item in value if str(item).strip()]
                if text_items:
                    messages.append(f"{key}: {'; '.join(text_items)}")
        return " | ".join(messages)

    def _cookies_to_header(self, jar: requests.cookies.RequestsCookieJar) -> str:
        parts = [f"{cookie.name}={cookie.value}" for cookie in jar]
        return "; ".join(parts)
