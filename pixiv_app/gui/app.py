from __future__ import annotations

import queue
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from pixiv_app.core.cookie_import import (
    cookie_domains,
    cookie_summary,
    parse_cookie_text,
)
from pixiv_app.core.plugin.manager import PluginManager
from pixiv_app.core.paths import downloads_root, plugin_roots
from pixiv_app.core.proxy_pool import ProxyInfo, ProxyPool, QuakeClient, parse_proxy_text
from pixiv_app.gui.proxy_dialog import ProxyDialog
from pixiv_app.services.gallery_api import GalleryApiServer


ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class DownloadResult:
    """Lightweight result struct (avoids dependency on deleted pixiv downloader)."""
    def __init__(self, *, work_id: int, total_pages: int, downloaded_pages: int,
                 skipped_pages: int, failed_pages: int, ok: bool, message: str):
        self.work_id = work_id
        self.total_pages = total_pages
        self.downloaded_pages = downloaded_pages
        self.skipped_pages = skipped_pages
        self.failed_pages = failed_pages
        self.ok = ok
        self.message = message


class SakuraDownloaderGUI:
    PLUGIN_MODE_MAP = {
        "自动解析": "plugin_auto",
    }
    LOGIN_MODE_MAP = {
        "Cookie 导入": "cookie",
    }

    BG_COLOR = "#eef7ff"
    SURFACE_COLOR = "#f8fbff"
    CARD_COLOR = "#ffffff"
    PRIMARY_COLOR = "#77b7ff"
    PRIMARY_HOVER = "#5da6f8"
    SECONDARY_COLOR = "#dbeeff"
    TEXT_COLOR = "#1e3a5f"
    MUTED_COLOR = "#6f8dac"
    BORDER_COLOR = "#d6e8fb"
    SUCCESS_COLOR = "#56b98a"
    WARNING_COLOR = "#f2b95e"
    DANGER_COLOR = "#eb7d7d"

    def __init__(self) -> None:
        self.root = ctk.CTk(fg_color=self.BG_COLOR)
        self.root.title("Sakura 下载器")
        self.root.geometry("1160x860")
        self.root.minsize(1020, 760)

        self.is_downloading = False
        self.stop_event = threading.Event()
        self.download_thread: threading.Thread | None = None
        self.gallery_thread: threading.Thread | None = None
        self.gallery_url = "http://127.0.0.1:8765"
        self.ui_queue: queue.Queue = queue.Queue()

        self.total_works = 0
        self.completed_works = 0
        self.success_works = 0
        self.failed_works = 0
        self.downloaded_pages = 0
        self.skipped_pages = 0
        self.failed_pages = 0

        self.proxy_config = {
            "enabled": False,
            "use_quake": False,
            "quake_mode": "api_v3",
            "quake_api_key": "",
            "quake_cookie": "",
            "manual_text": "",
            "countries_text": "US,JP,KR,SG,HK,TW,DE,FR,GB,CA,AU",
        }
        self.proxy_summary_var = ctk.StringVar(value="未启用代理池")
        self.target_mode_var = ctk.StringVar(value="自动解析")
        self.login_mode_var = ctk.StringVar(value="Cookie 导入")
        self.auth_status_var = ctk.StringVar(value="推荐导入 cookies.txt 以使用需要登录的插件。")
        self.cookie_json: list[dict] = []

        self.setup_ui()
        self.process_ui_queue()
        self.update_target_mode_ui()
        self.update_login_mode_ui()
        self.root.after(500, self.show_usage_notice)

    def setup_ui(self) -> None:
        self.create_header()
        self.create_body()
        self.create_footer()

    def create_header(self) -> None:
        header = ctk.CTkFrame(self.root, fg_color=self.SURFACE_COLOR, corner_radius=0, border_width=0, height=108)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header,
            text="Sakura 下载器",
            text_color=self.TEXT_COLOR,
            font=ctk.CTkFont(size=30, weight="bold"),
        ).pack(anchor="w", padx=28, pady=(18, 4))
        sub = ctk.CTkFrame(header, fg_color="transparent")
        sub.pack(fill="x", padx=28, pady=(0, 14))
        ctk.CTkLabel(
            sub,
            text="插件驱动的本地媒体下载框架。导入 Python 插件即可支持任意站点。",
            text_color=self.MUTED_COLOR,
            font=ctk.CTkFont(size=14),
        ).pack(side="left", anchor="w")
        ctk.CTkButton(
            sub,
            text="插件管理",
            width=100,
            height=32,
            corner_radius=10,
            fg_color=self.SECONDARY_COLOR,
            text_color=self.TEXT_COLOR,
            hover_color=self.PRIMARY_HOVER,
            command=self.open_plugin_panel,
        ).pack(side="right")

    def create_body(self) -> None:
        body = ctk.CTkFrame(self.root, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=22, pady=18)
        body.grid_columnconfigure(0, weight=7)
        body.grid_columnconfigure(1, weight=5)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(body, fg_color="transparent", corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        right = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew")

        self.create_auth_card(left)
        self.create_input_card(left)
        self.create_settings_card(left)
        self.create_tips_card(left)
        self.create_overview_card(right)
        self.create_progress_card(right)
        self.create_log_card(right)

    def make_card(self, parent, title: str, desc: str | None = None) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=self.CARD_COLOR, corner_radius=20, border_width=1, border_color=self.BORDER_COLOR)
        card.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(card, text=title, text_color=self.TEXT_COLOR, font=ctk.CTkFont(size=20, weight="bold")).pack(
            anchor="w", padx=20, pady=(18, 4)
        )
        if desc:
            ctk.CTkLabel(
                card,
                text=desc,
                text_color=self.MUTED_COLOR,
                font=ctk.CTkFont(size=13),
                wraplength=520,
                justify="left",
            ).pack(anchor="w", padx=20, pady=(0, 12))
        return card

    def create_auth_card(self, parent) -> None:
        card = self.make_card(parent, "Cookie 导入", "导入 Get cookies.txt 导出的 txt/json 文件，供需要登录的插件使用。")

        cookie_row = ctk.CTkFrame(card, fg_color="transparent")
        cookie_row.pack(fill="x", padx=20, pady=(0, 14))
        ctk.CTkLabel(
            cookie_row,
            text="Cookie txt",
            text_color=self.TEXT_COLOR,
            font=ctk.CTkFont(size=14, weight="bold"),
            width=78,
        ).pack(side="left", padx=(0, 10))
        self.cookie_import_button = ctk.CTkButton(
            cookie_row,
            text="导入 cookies.txt / json",
            command=self.import_cookie_txt,
            height=42,
            corner_radius=14,
            fg_color="#f7fbff",
            border_width=2,
            border_color=self.BORDER_COLOR,
            text_color="#6f7f91",
            hover_color="#e8f3ff",
        )
        self.cookie_import_button.pack(side="left", fill="x", expand=True)

        status_row = ctk.CTkFrame(card, fg_color="#f7fbff", corner_radius=12)
        status_row.pack(fill="x", padx=20, pady=(0, 18))
        ctk.CTkLabel(
            status_row,
            textvariable=self.auth_status_var,
            text_color="#365b85",
            font=ctk.CTkFont(size=12),
            justify="left",
            wraplength=520,
        ).pack(anchor="w", padx=12, pady=8)

    def create_input_card(self, parent) -> None:
        card = self.make_card(
            parent,
            "抓取目标",
            "输入链接或关键词，程序会自动匹配已导入插件的 can_handle() 进行解析和下载。",
        )

        mode_row = ctk.CTkFrame(card, fg_color="transparent")
        mode_row.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(mode_row, text="模式", text_color=self.TEXT_COLOR, font=ctk.CTkFont(size=14, weight="bold"), width=78).pack(
            side="left", padx=(0, 10)
        )
        self.target_mode_menu = ctk.CTkOptionMenu(
            mode_row,
            values=list(self.PLUGIN_MODE_MAP.keys()),
            variable=self.target_mode_var,
            command=self.update_target_mode_ui,
            width=220,
        )
        self.target_mode_menu.pack(side="left")

        self.target_entry = self.create_labeled_entry(card, "目标", "输入链接、ID 或关键词；程序会自动匹配已导入插件的 can_handle()")
        self.mode_hint_label = ctk.CTkLabel(
            card,
            text="自动解析：从已导入插件中选择第一个 can_handle() 返回 True 的插件。",
            text_color=self.MUTED_COLOR,
            font=ctk.CTkFont(size=12),
            justify="left",
        )
        self.mode_hint_label.pack(anchor="w", padx=20, pady=(0, 10))

        keyword_row = ctk.CTkFrame(card, fg_color="transparent")
        keyword_row.pack(fill="x", padx=20, pady=(0, 12))
        self.keyword_limit_label = ctk.CTkLabel(keyword_row, text="抓取数量", text_color=self.TEXT_COLOR, font=ctk.CTkFont(size=14, weight="bold"), width=78)
        self.keyword_limit_label.pack(side="left", padx=(0, 10))
        self.keyword_limit_entry = ctk.CTkEntry(
            keyword_row,
            width=120,
            height=42,
            corner_radius=14,
            fg_color="#f7fbff",
            border_color=self.BORDER_COLOR,
        )
        self.keyword_limit_entry.insert(0, "20")
        self.keyword_limit_entry.pack(side="left")
        self.keyword_row_frame = keyword_row

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(4, 18))
        self.hero_start_button = ctk.CTkButton(
            row,
            text="开始抓取",
            command=self.start_download,
            height=46,
            width=180,
            corner_radius=999,
            fg_color=self.PRIMARY_COLOR,
            hover_color=self.PRIMARY_HOVER,
            text_color="#ffffff",
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self.hero_start_button.pack(side="left")

    def create_settings_card(self, parent) -> None:
        card = self.make_card(parent, "性能设置", "代理模式下线程数会自动限制在 3 到 6 之间，以降低触发限流的概率。")
        self.thread_slider, _ = self.create_slider_row(card, "下载线程", 2, 16, 14, 8, lambda value: f"{int(value)}")
        self.delay_slider, _ = self.create_slider_row(card, "请求间隔", 0.0, 1.5, 15, 0.1, lambda value: f"{value:.1f}s")

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 14))
        self.proxy_button = ctk.CTkButton(row, text="代理设置", command=self.open_proxy_dialog, width=120, height=38, corner_radius=12)
        self.proxy_button.pack(side="left")
        ctk.CTkLabel(row, textvariable=self.proxy_summary_var, text_color=self.MUTED_COLOR, font=ctk.CTkFont(size=12)).pack(
            side="left", padx=10
        )

    def create_tips_card(self, parent) -> None:
        card = self.make_card(parent, "使用说明")
        for tip in [
            "导入 Cookie 后，需要登录的插件可以读取这些 Cookie 进行鉴权。",
            "「插件管理」中可以导入自定义 Python 插件，扩展下载能力。",
            "「打开缩略图墙」可浏览本地已下载的作品，支持标签和搜索。",
            "代理池可以在「代理设置」中配置，支持手动代理和 Quake API 自动获取。",
        ]:
            ctk.CTkLabel(card, text=f"- {tip}", text_color=self.MUTED_COLOR, font=ctk.CTkFont(size=13), justify="left").pack(
                anchor="w", padx=20, pady=(0, 8)
            )
        ctk.CTkLabel(card, text="", height=8).pack()

    def create_overview_card(self, parent) -> None:
        card = self.make_card(parent, "任务概览")
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=(2, 18))
        grid.grid_columnconfigure((0, 1), weight=1)
        self.works_card = self.create_stat_panel(grid, 0, 0, "作品进度", "0 / 0", self.PRIMARY_COLOR)
        self.success_card = self.create_stat_panel(grid, 0, 1, "成功项目", "0", self.SUCCESS_COLOR)
        self.pages_card = self.create_stat_panel(grid, 1, 0, "新下载页数", "0", self.WARNING_COLOR)
        self.skip_card = self.create_stat_panel(grid, 1, 1, "已跳过页数", "0", self.SECONDARY_COLOR)

        gallery_row = ctk.CTkFrame(card, fg_color="transparent")
        gallery_row.pack(fill="x", padx=20, pady=(0, 18))
        self.gallery_button = ctk.CTkButton(
            gallery_row,
            text="打开缩略图墙",
            command=self.open_gallery_wall,
            height=42,
            width=150,
            corner_radius=14,
            fg_color=self.SUCCESS_COLOR,
            hover_color="#3f9d72",
        )
        self.gallery_button.pack(side="left")
        ctk.CTkLabel(
            gallery_row,
            text="浏览本地图库、搜索标签、查看收藏",
            text_color=self.MUTED_COLOR,
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=10)

    def create_progress_card(self, parent) -> None:
        card = self.make_card(parent, "实时进度")
        self.progress_bar = ctk.CTkProgressBar(card, progress_color=self.PRIMARY_COLOR, fg_color=self.SECONDARY_COLOR, corner_radius=999, height=20)
        self.progress_bar.pack(fill="x", padx=20, pady=(4, 12))
        self.progress_bar.set(0)
        self.progress_label = ctk.CTkLabel(card, text="准备开始", text_color=self.TEXT_COLOR, font=ctk.CTkFont(size=14, weight="bold"))
        self.progress_label.pack(anchor="w", padx=20)
        self.stats_label = ctk.CTkLabel(card, text="失败项目 0 | 失败页数 0", text_color=self.MUTED_COLOR, font=ctk.CTkFont(size=13))
        self.stats_label.pack(anchor="w", padx=20, pady=(4, 18))

    def create_log_card(self, parent) -> None:
        card = self.make_card(parent, "抓取日志")
        card.pack_configure(fill="both", expand=True)
        self.log_text = ctk.CTkTextbox(
            card,
            height=520,
            corner_radius=16,
            fg_color="#f4f9ff",
            border_width=1,
            border_color=self.BORDER_COLOR,
            text_color=self.TEXT_COLOR,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.log_text.pack(fill="both", expand=True, padx=20, pady=(0, 20))

    def create_footer(self) -> None:
        footer = ctk.CTkFrame(self.root, fg_color=self.SURFACE_COLOR, corner_radius=0, height=92)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        row = ctk.CTkFrame(footer, fg_color="transparent")
        row.pack(expand=True, pady=12)

        self.start_button = ctk.CTkButton(row, text="开始抓取", command=self.start_download, width=220, height=50, corner_radius=999)
        self.start_button.pack(side="left", padx=8)

        self.stop_button = ctk.CTkButton(
            row,
            text="停止任务",
            command=self.stop_download,
            width=160,
            height=50,
            corner_radius=999,
            fg_color=self.DANGER_COLOR,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=8)

    def create_labeled_entry(self, parent, label_text: str, placeholder: str, show: str | None = None) -> ctk.CTkEntry:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 14))
        ctk.CTkLabel(row, text=label_text, text_color=self.TEXT_COLOR, font=ctk.CTkFont(size=14, weight="bold"), width=78).pack(
            side="left", padx=(0, 10)
        )
        entry = ctk.CTkEntry(
            row,
            placeholder_text=placeholder,
            height=42,
            corner_radius=14,
            fg_color="#f7fbff",
            border_color=self.BORDER_COLOR,
            show=show or "",
        )
        entry.pack(side="left", fill="x", expand=True)
        return entry

    def create_slider_row(self, parent, name, from_, to, steps, default, formatter):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkLabel(row, text=name, text_color=self.TEXT_COLOR, font=ctk.CTkFont(size=14, weight="bold"), width=78).pack(
            side="left", padx=(0, 10)
        )
        value_label = ctk.CTkLabel(row, text=formatter(default), text_color=self.PRIMARY_HOVER, font=ctk.CTkFont(size=13, weight="bold"), width=52)
        value_label.pack(side="right")
        slider = ctk.CTkSlider(row, from_=from_, to=to, number_of_steps=steps, progress_color=self.PRIMARY_COLOR)
        slider.pack(side="left", fill="x", expand=True, padx=(0, 12))
        slider.set(default)
        slider.configure(command=lambda value: value_label.configure(text=formatter(value)))
        return slider, value_label

    def create_stat_panel(self, parent, row, column, title, value, color):
        panel = ctk.CTkFrame(parent, fg_color="#f7fbff", corner_radius=18, border_width=1, border_color=self.BORDER_COLOR)
        panel.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)
        ctk.CTkFrame(panel, width=12, height=12, corner_radius=999, fg_color=color).pack(anchor="w", padx=16, pady=(14, 8))
        ctk.CTkLabel(panel, text=title, text_color=self.MUTED_COLOR, font=ctk.CTkFont(size=13)).pack(anchor="w", padx=16)
        label = ctk.CTkLabel(panel, text=value, text_color=self.TEXT_COLOR, font=ctk.CTkFont(size=24, weight="bold"))
        label.pack(anchor="w", padx=16, pady=(2, 16))
        return label

    # ── UI queue ──────────────────────────────────────────────

    def process_ui_queue(self) -> None:
        while True:
            try:
                callback, args = self.ui_queue.get_nowait()
                callback(*args)
            except queue.Empty:
                break
        self.root.after(100, self.process_ui_queue)

    def enqueue_ui(self, callback, *args) -> None:
        self.ui_queue.put((callback, args))

    def log_message(self, message: str) -> None:
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see("end")

    # ── Input state ───────────────────────────────────────────

    def set_inputs_state(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.target_entry.configure(state=state)
        self.proxy_button.configure(state=state)
        self.target_mode_menu.configure(state=state)
        if hasattr(self, "cookie_import_button"):
            self.cookie_import_button.configure(state=state)
        self.keyword_limit_entry.configure(state=state)

    def update_login_mode_ui(self, _choice: str | None = None) -> None:
        pass  # Only one mode; no-op

    def update_target_mode_ui(self, _choice: str | None = None) -> None:
        self.target_entry.configure(placeholder_text="输入链接、ID 或关键词；程序会自动匹配已导入插件的 can_handle()")
        self.keyword_limit_label.configure(text="抓取数量")
        self.keyword_limit_entry.configure(state="normal")
        self.mode_hint_label.configure(
            text="自动解析：从已导入插件中选择第一个 can_handle() 返回 True 的插件。",
            text_color=self.MUTED_COLOR,
        )

    # ── Cookie import ─────────────────────────────────────────

    def import_cookie_txt(self) -> None:
        path = filedialog.askopenfilename(
            title="导入 Cookie txt",
            filetypes=[
                ("Cookie files", "*.txt *.json"),
                ("Text files", "*.txt"),
                ("JSON files", "*.json"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        try:
            cookies = parse_cookie_text(text)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return

        self.cookie_json = cookies
        summary = cookie_summary(cookies)
        self.set_cookie_indicator(f"已导入 {Path(path).name}（{len(cookies)} 个 Cookie）")
        self.auth_status_var.set(f"已从 txt 导入 Cookie：{summary}")
        self.log_message(f"已导入 Cookie txt: {path} ({summary})")
        domain_note = ", ".join(cookie_domains(cookies)[:4])
        messagebox.showinfo("导入完成", f"已解析 Cookie。\n{summary}\n域名: {domain_note}")

    def set_cookie_indicator(self, text: str) -> None:
        self.cookie_import_button.configure(text=text)

    # ── Usage notice ──────────────────────────────────────────

    def show_usage_notice(self) -> None:
        if getattr(self, "_notice_window", None) is not None:
            try:
                self._notice_window.focus()
                return
            except Exception:
                self._notice_window = None

        pages = [
            (
                "【使用说明】",
                [
                    "Sakura 下载器是一个插件驱动的本地媒体下载框架。",
                    "通过导入 Python 插件，可以扩展对任意站点的支持。",
                    "点击「插件管理」导入自定义插件，或使用「生成模板」快速创建新插件。",
                ],
            ),
            (
                "【Cookie 导入】",
                [
                    "1. 使用浏览器插件（如 Get cookies.txt）导出登录态",
                    "2. 点击「导入 cookies.txt / json」导入 Cookie",
                    "3. 插件可通过 cookie_json 属性读取登录信息",
                ],
            ),
            (
                "【注意事项】",
                [
                    "平台可能会对异常访问行为进行限流或验证",
                    "不建议长时间连续运行批量任务",
                    "若出现无法访问或登录失效，请重新获取 cookie 后再尝试",
                ],
            ),
            (
                "【用户须知】",
                [
                    "爬虫运行有风险，请合理使用",
                    "本工具只做研究学习使用，严禁非法搬运",
                    "若遇到问题，请联系作者",
                    "QQ:2811043066",
                    "博客:http://blog.kunkunxiaomao.top",
                ]
            )
        ]

        notice_width = 680
        notice_height = 520

        win = ctk.CTkToplevel(self.root)
        self._notice_window = win
        win.title("用户须知")
        win.geometry(f"{notice_width}x{notice_height}")
        win.minsize(620, 480)
        win.configure(fg_color=self.BG_COLOR)
        win.transient(self.root)
        win.grab_set()

        def close_notice() -> None:
            self._notice_window = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_notice)

        outer = ctk.CTkFrame(win, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=20, pady=20)

        card = ctk.CTkFrame(
            outer,
            fg_color=self.CARD_COLOR,
            corner_radius=18,
            border_width=1,
            border_color=self.BORDER_COLOR,
        )
        card.pack(fill="both", expand=True)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=24, pady=(20, 8))
        badge = ctk.CTkFrame(top, width=42, height=42, corner_radius=14, fg_color=self.SECONDARY_COLOR)
        badge.pack(side="left", padx=(0, 12))
        badge.pack_propagate(False)
        ctk.CTkLabel(
            badge,
            text="!",
            text_color=self.TEXT_COLOR,
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(expand=True)
        ctk.CTkLabel(
            top,
            text="用户须知",
            text_color=self.TEXT_COLOR,
            font=ctk.CTkFont(size=23, weight="bold"),
        ).pack(side="left")

        page_label = ctk.CTkLabel(card, text="", text_color=self.MUTED_COLOR, font=ctk.CTkFont(size=12))
        page_label.pack(anchor="w", padx=24, pady=(0, 8))

        content = ctk.CTkFrame(card, fg_color="#f7fbff", corner_radius=16, border_width=1, border_color=self.BORDER_COLOR)
        content.pack(fill="both", expand=True, padx=24, pady=(0, 14))

        title_label = ctk.CTkLabel(
            content,
            text="",
            text_color=self.TEXT_COLOR,
            font=ctk.CTkFont(size=19, weight="bold"),
            justify="left",
        )
        title_label.pack(anchor="w", padx=20, pady=(16, 8))

        body_frame = ctk.CTkScrollableFrame(
            content,
            fg_color="transparent",
            scrollbar_button_color=self.SECONDARY_COLOR,
            scrollbar_button_hover_color=self.PRIMARY_COLOR,
        )
        body_frame.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.pack(fill="x", padx=24, pady=(0, 20))

        page_index = {"value": 0}

        def render_page() -> None:
            title, lines = pages[page_index["value"]]
            title_label.configure(text=title)
            page_label.configure(text=f"{page_index['value'] + 1} / {len(pages)}")
            for child in body_frame.winfo_children():
                child.destroy()
            for line in lines:
                ctk.CTkLabel(
                    body_frame,
                    text=line,
                    text_color="#365b85",
                    font=ctk.CTkFont(size=13),
                    wraplength=560,
                    justify="left",
                ).pack(anchor="w", fill="x", padx=(0, 8), pady=(0, 8))
            prev_button.configure(state="normal" if page_index["value"] > 0 else "disabled")
            next_button.configure(text="我知道了" if page_index["value"] == len(pages) - 1 else "下一页")

        def previous_page() -> None:
            if page_index["value"] > 0:
                page_index["value"] -= 1
                render_page()

        def next_page() -> None:
            if page_index["value"] >= len(pages) - 1:
                close_notice()
                return
            page_index["value"] += 1
            render_page()

        prev_button = ctk.CTkButton(
            footer,
            text="上一页",
            command=previous_page,
            width=110,
            height=38,
            corner_radius=12,
            fg_color=self.SECONDARY_COLOR,
            hover_color="#cce7ff",
            text_color=self.TEXT_COLOR,
        )
        prev_button.pack(side="left")
        next_button = ctk.CTkButton(
            footer,
            text="下一页",
            command=next_page,
            width=130,
            height=38,
            corner_radius=12,
            fg_color=self.PRIMARY_COLOR,
            hover_color=self.PRIMARY_HOVER,
            text_color="#ffffff",
        )
        next_button.pack(side="right")

        self.root.update_idletasks()
        x = self.root.winfo_x() + max((self.root.winfo_width() - notice_width) // 2, 0)
        y = self.root.winfo_y() + max((self.root.winfo_height() - notice_height) // 2, 0)
        win.geometry(f"{notice_width}x{notice_height}+{x}+{y}")
        render_page()

    # ── Plugin panel ──────────────────────────────────────────

    def open_plugin_panel(self) -> None:
        from pixiv_app.gui.plugin_panel import PluginPanelWindow

        PluginPanelWindow(self.root)

    # ── Proxy ─────────────────────────────────────────────────

    def open_proxy_dialog(self) -> None:
        ProxyDialog(self.root, self.proxy_config, self.save_proxy_config, self.handle_proxy_dialog_action)

    def save_proxy_config(self, config: dict) -> None:
        self.proxy_config = config
        manual_count = len(parse_proxy_text(config.get("manual_text", "")))
        if not config.get("enabled"):
            self.proxy_summary_var.set("未启用代理池")
            return
        summary = f"已启用，列表中 {manual_count} 条"
        if config.get("use_quake"):
            summary += f" + Quake({config.get('quake_mode', 'api_v3')})"
        self.proxy_summary_var.set(summary)

    def format_proxy_text(self, proxies: list[ProxyInfo]) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        for proxy in proxies:
            if proxy.proxy_url not in seen:
                seen.add(proxy.proxy_url)
                lines.append(proxy.proxy_url)
        return "\n".join(lines)

    def handle_proxy_dialog_action(self, action: str, config: dict) -> str | dict[str, str]:
        if action != "fetch_preview":
            return "未知操作"
        manual_text = str(config.get("manual_text", "")).strip()
        countries_text = str(config.get("countries_text", "")).strip()
        countries = [item.strip().upper() for item in countries_text.split(",") if item.strip()]
        pool = ProxyPool()
        manual_proxies = parse_proxy_text(manual_text)
        if manual_proxies:
            pool.add_proxies(manual_proxies)
        if config.get("use_quake"):
            quake = QuakeClient(
                api_key=str(config.get("quake_api_key", "")),
                cookie=str(config.get("quake_cookie", "")),
                mode=str(config.get("quake_mode", "api_v3")),
            )
            pool.add_proxies(quake.get_foreign_proxies(size=80, countries=countries or None))
        total = len(pool.proxies)
        if total == 0:
            return {"status": "没有找到可校验的代理，请检查输入。", "manual_text": manual_text}
        working = pool.verify_all(max_workers=12, max_proxies=min(total, 80))
        merged_text = self.format_proxy_text(working) if working else manual_text
        return {"status": f"预校验完成：可用 {len(working)} / 总计 {total}，已回填到代理列表。", "manual_text": merged_text}

    # ── Gallery ───────────────────────────────────────────────

    def open_gallery_wall(self) -> None:
        try:
            url = self.ensure_gallery_service()
            webbrowser.open(url)
            self.log_message(f"已打开缩略图墙: {url}")
        except Exception as exc:
            message = f"无法打开缩略图墙: {exc}"
            self.log_message(message)
            messagebox.showerror("打开失败", message)

    def ensure_gallery_service(self) -> str:
        for port in (8765, 8766, 8767):
            url = f"http://127.0.0.1:{port}"
            if self.is_gallery_wall_ready(url):
                self.gallery_url = url
                return url

        last_error: Exception | None = None
        for port in (8765, 8766, 8767):
            url = f"http://127.0.0.1:{port}"
            if self.is_port_occupied(url):
                continue
            try:
                thread = threading.Thread(
                    target=GalleryApiServer(host="127.0.0.1", port=port).serve_forever,
                    daemon=True,
                    name=f"gallery-api-{port}",
                )
                thread.start()
                self.gallery_thread = thread
                for _ in range(20):
                    if self.is_gallery_wall_ready(url):
                        self.gallery_url = url
                        return url
                    time.sleep(0.1)
            except Exception as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error
        raise RuntimeError("本地图库端口被占用，且没有可用的缩略图墙服务。")

    def is_gallery_wall_ready(self, url: str) -> bool:
        try:
            with urllib.request.urlopen(url + "/", timeout=1.5) as response:
                body = response.read(4096).decode("utf-8", errors="ignore")
            return "Sakura Local Gallery" in body
        except (OSError, urllib.error.URLError):
            return False

    def is_port_occupied(self, url: str) -> bool:
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1.0).close()
            return True
        except (OSError, urllib.error.URLError):
            return False

    # ── Counters ──────────────────────────────────────────────

    def reset_counters(self) -> None:
        self.total_works = 0
        self.completed_works = 0
        self.success_works = 0
        self.failed_works = 0
        self.downloaded_pages = 0
        self.skipped_pages = 0
        self.failed_pages = 0
        self.refresh_overview()

    def refresh_overview(self) -> None:
        self.works_card.configure(text=f"{self.completed_works} / {self.total_works}")
        self.success_card.configure(text=str(self.success_works))
        self.pages_card.configure(text=str(self.downloaded_pages))
        self.skip_card.configure(text=str(self.skipped_pages))
        progress = (self.completed_works / self.total_works) if self.total_works else 0
        self.progress_bar.set(progress)
        self.progress_label.configure(text=f"当前进度 {self.completed_works}/{self.total_works}")
        self.stats_label.configure(text=f"失败项目 {self.failed_works} | 失败页数 {self.failed_pages}")

    def update_stage_progress(self, text: str, progress: float | None = None) -> None:
        if progress is not None:
            self.progress_bar.set(max(0.0, min(1.0, float(progress))))
        self.progress_label.configure(text=text)

    def set_total_works(self, total: int) -> None:
        self.total_works = total
        self.refresh_overview()
        self.progress_label.configure(text="正在抓取...")

    # ── Plugin manager ────────────────────────────────────────

    def _plugin_manager(self) -> PluginManager:
        if getattr(self, "_cached_plugin_manager", None) is None:
            self._cached_plugin_manager = PluginManager(plugin_roots())
            self._cached_plugin_manager.load_all()
        return self._cached_plugin_manager

    def _plugin_max_items(self) -> int:
        value = self.keyword_limit_entry.get().strip() or "20"
        if not value.isdigit():
            raise ValueError("抓取数量必须是正整数。")
        return max(1, min(int(value), 200))

    # ── Download flow ─────────────────────────────────────────

    def start_download(self) -> None:
        if self.is_downloading:
            return

        target = self.target_entry.get().strip()
        if not target:
            messagebox.showerror("输入错误", "请输入插件解析目标。")
            return
        try:
            max_items = self._plugin_max_items()
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc))
            return

        self.reset_counters()
        self.is_downloading = True
        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.hero_start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.set_inputs_state(False)
        self.progress_label.configure(text="正在匹配插件...")
        self.log_message(f"准备开始：目标={target[:120]}")

        self.download_thread = threading.Thread(
            target=self.run_download_generic_plugin_thread,
            args=(target, max_items),
            daemon=True,
        )
        self.download_thread.start()

    def stop_download(self) -> None:
        if not self.is_downloading:
            return
        self.stop_event.set()
        self.log_message("已请求停止，正在等待当前任务收尾...")
        self.progress_label.configure(text="正在停止任务...")

    def finalize_ui(self) -> None:
        self.is_downloading = False
        self.start_button.configure(state="normal")
        self.hero_start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.set_inputs_state(True)

    def handle_result(self, result: DownloadResult, completed: int, total: int) -> None:
        self.completed_works = completed
        self.total_works = total
        self.downloaded_pages += result.downloaded_pages
        self.skipped_pages += result.skipped_pages
        self.failed_pages += result.failed_pages
        if result.ok:
            self.success_works += 1
        else:
            self.failed_works += 1
        self.refresh_overview()
        self.log_message(result.message)

    def handle_finish(self, stopped: bool) -> None:
        self.refresh_overview()
        self.finalize_ui()
        if stopped:
            self.progress_label.configure(text="任务已停止")
            messagebox.showinfo("任务停止", f"已完成项目 {self.completed_works}/{self.total_works}")
        else:
            self.progress_label.configure(text="抓取完成")
            messagebox.showinfo("抓取完成", f"项目总数 {self.total_works}\n成功项目 {self.success_works}")

    def handle_error(self, message: str) -> None:
        self.log_message(message)
        self.progress_label.configure(text="任务异常")
        self.finalize_ui()
        messagebox.showerror("运行错误", message)

    def run_download_generic_plugin_thread(self, target: str, max_items: int = 20) -> None:
        """Auto-match a plugin by can_handle(), then parse and download."""
        try:
            manager = self._plugin_manager()
            manager.reload_all_from_disk()
            plugin = manager.get_plugin_for_url(target)
            if plugin is None:
                self.enqueue_ui(
                    self.handle_error,
                    "没有插件能处理该目标。请在「插件管理」导入插件，并确认插件 can_handle() 能识别该输入。",
                )
                return
            if not plugin.validate():
                self.enqueue_ui(self.handle_error, f"插件不可用或需配置: {plugin.name}")
                return

            self.enqueue_ui(self.log_message, f"已匹配插件：{plugin.name}，正在解析…")
            resources = plugin.parse(target)[:max_items]
            if self.stop_event.is_set():
                self.enqueue_ui(self.handle_finish, True)
                return
            if not resources:
                self.enqueue_ui(self.handle_error, f"插件 {plugin.name} 解析结果为空。")
                return

            total = len(resources)
            self.enqueue_ui(self.set_total_works, total)
            save_root = downloads_root()
            completed = 0
            for i, res in enumerate(resources):
                if self.stop_event.is_set():
                    break
                try:
                    paths = plugin.download(res, save_root)
                    ok = len(paths) > 0
                    pages = len(res.files) if res.files else max(len(paths), 1)
                    downloaded = len(paths)
                    title = res.title or res.id or f"项目 {i + 1}"
                    result = DownloadResult(
                        work_id=i,
                        total_pages=max(pages, 1),
                        downloaded_pages=downloaded if ok else 0,
                        skipped_pages=0,
                        failed_pages=0 if ok else max(1, pages),
                        ok=ok,
                        message=f"[{plugin.name}] {title[:60]} — 保存文件 {downloaded}/{max(pages, 1)}",
                    )
                except Exception as exc:
                    result = DownloadResult(
                        work_id=i,
                        total_pages=1,
                        downloaded_pages=0,
                        skipped_pages=0,
                        failed_pages=1,
                        ok=False,
                        message=f"[{plugin.name}] 下载失败: {exc}",
                    )
                completed += 1
                self.enqueue_ui(self.handle_result, result, completed, total)

            self.enqueue_ui(self.handle_finish, self.stop_event.is_set())
        except Exception as exc:
            self.enqueue_ui(self.handle_error, f"插件自动解析出错: {exc}")

    def run(self) -> None:
        self.root.mainloop()
