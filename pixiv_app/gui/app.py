from __future__ import annotations

import queue
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
from urllib.parse import quote

import customtkinter as ctk

from pixiv_app.core.auth import AuthResult, PixivAuthClient, SessionStore
from pixiv_app.core.cookie_import import (
    cookie_domains,
    cookie_summary,
    cookies_to_header,
    cookies_to_playwright,
    has_cookie_domain,
    parse_cookie_text,
    save_playwright_cookies,
)
from pixiv_app.core.downloader import (
    DownloadResult,
    PixivRequestError,
    download_illust,
    download_keyword_works,
    download_novel,
    download_user_works,
    fetch_user_work_ids,
    parse_pixiv_target,
    resolve_workers,
    search_illust_ids_by_keyword,
)
from pixiv_app.core.plugin.manager import PluginManager
from pixiv_app.core.paths import app_session_file, downloads_root, plugin_roots, plugins_root, runtime_path
from pixiv_app.core.proxy_pool import ProxyInfo, ProxyPool, QuakeClient, parse_proxy_text
from pixiv_app.gui.proxy_dialog import ProxyDialog
from pixiv_app.runtime.enqueue import collect_task_specs, enqueue_specs_to_library
from pixiv_app.runtime.worker_loop import run_queue_until_idle
from pixiv_app.services.gallery_api import GalleryApiServer
from pixiv_app.tasks.parser import parse_batch_for_mode


ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class SakuraDownloaderGUI:
    TARGET_MODE_MAP = {
        "作者全部作品": "user",
        "单个作品": "illust",
        "文章/小说": "novel",
        "关键词图片": "keyword",
    }
    X_TARGET_MODE_MAP = {
        "作者媒体": "x_user",
        "单个推文": "x_status",
        "搜索词媒体": "x_keyword",
    }
    XHS_TARGET_MODE_MAP = {
        "搜索关键词": "xhs_keyword",
        "单篇笔记": "xhs_note",
        "链接解析": "xhs_link",
    }
    GENERIC_PLUGIN_MODE_MAP = {
        "自动解析": "plugin_auto",
    }
    PLACEHOLDER_MAP = {
        "user": "请输入作者 ID，或作品链接自动反查作者",
        "illust": "请输入作品 ID 或作品链接",
        "novel": "请输入小说 ID 或小说链接",
        "keyword": "请输入关键词，例如 初音未来、猫耳",
    }
    X_PLACEHOLDER_MAP = {
        "x_user": "请输入 X 作者名，例如 nasa 或 @nasa",
        "x_status": "请输入 X / Twitter 单条推文链接",
        "x_keyword": "请输入搜索词，例如 anime art filter:media",
    }
    XHS_PLACEHOLDER_MAP = {
        "xhs_keyword": "请输入小红书搜索关键词，例如 穿搭、插画、摄影",
        "xhs_note": "请输入小红书笔记链接，例如 https://www.xiaohongshu.com/explore/...",
        "xhs_link": "请输入小红书链接或 xhslink.com 分享链接",
    }
    LOGIN_MODE_MAP = {
        "Cookie 登录": "cookie",
        "账号密码登录": "password",
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
        self.use_queue_var = ctk.BooleanVar(value=True)
        self.incremental_wm_var = ctk.BooleanVar(value=True)
        self.platform_var = ctk.StringVar(value="Pixiv")
        self.target_mode_var = ctk.StringVar(value="作者全部作品")
        self.login_mode_var = ctk.StringVar(value="Cookie 登录")
        self.auth_status_var = ctk.StringVar(value="未登录，推荐导入 cookies.txt；也可尝试账号密码登录。")
        self.cookie_json: list[dict] = []
        self.cookie_header = ""

        self.auth_client = PixivAuthClient()
        self.session_store = SessionStore(app_session_file())

        self.setup_ui()
        self.process_ui_queue()
        self.update_target_mode_ui(self.target_mode_var.get())
        self.update_crawler_platform_ui()
        self.update_login_mode_ui(self.login_mode_var.get())
        self.load_saved_session(silent=True)
        self.root.after(500, self.show_login_warning)

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
            text="支持 Pixiv、X / Twitter、小红书与可导入 Python 插件的本地媒体下载。",
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
        card = self.make_card(parent, "登录方式", "推荐导入 Get cookies.txt 导出的 txt/json 文件；程序会解析成 JSON 保存，抓取时再交给后端请求层使用。")

        mode_row = ctk.CTkFrame(card, fg_color="transparent")
        mode_row.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(mode_row, text="方式", text_color=self.TEXT_COLOR, font=ctk.CTkFont(size=14, weight="bold"), width=78).pack(
            side="left", padx=(0, 10)
        )
        self.login_mode_menu = ctk.CTkOptionMenu(
            mode_row,
            values=list(self.LOGIN_MODE_MAP.keys()),
            variable=self.login_mode_var,
            command=self.update_login_mode_ui,
            width=220,
        )
        self.login_mode_menu.pack(side="left")

        self.cookie_frame = ctk.CTkFrame(card, fg_color="transparent")
        cookie_row = ctk.CTkFrame(self.cookie_frame, fg_color="transparent")
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

        self.password_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.login_id_entry = self.create_labeled_entry(
            self.password_frame,
            "账号",
            "请输入邮箱、Pixiv ID 或登录账号",
        )
        self.password_entry = self.create_labeled_entry(
            self.password_frame,
            "密码",
            "请输入密码；不会写入源码，只用于本次尝试登录",
            show="*",
        )

        action_row = ctk.CTkFrame(card, fg_color="transparent")
        action_row.pack(fill="x", padx=20, pady=(4, 10))
        self.load_session_button = ctk.CTkButton(action_row, text="自动载入", width=110, command=self.load_saved_session)
        self.load_session_button.pack(side="left")
        self.validate_session_button = ctk.CTkButton(action_row, text="检测会话", width=110, command=self.validate_session)
        self.validate_session_button.pack(side="left", padx=(10, 0))
        self.login_button = ctk.CTkButton(action_row, text="尝试登录", width=110, command=self.login_with_password)
        self.login_button.pack(side="left", padx=(10, 0))
        self.save_session_button = ctk.CTkButton(action_row, text="保存会话", width=110, command=self.save_session)
        self.save_session_button.pack(side="left", padx=(10, 0))

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
            "先选择平台：Pixiv 使用下方模式与队列；插件自动识别会按 can_handle() 匹配已导入插件。",
        )

        plat_row = ctk.CTkFrame(card, fg_color="transparent")
        plat_row.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(plat_row, text="平台", text_color=self.TEXT_COLOR, font=ctk.CTkFont(size=14, weight="bold"), width=78).pack(
            side="left", padx=(0, 10)
        )
        self.platform_menu = ctk.CTkOptionMenu(
            plat_row,
            values=["Pixiv", "X / Twitter", "小红书", "插件自动识别"],
            variable=self.platform_var,
            command=self.update_crawler_platform_ui,
            width=200,
        )
        self.platform_menu.pack(side="left")

        mode_row = ctk.CTkFrame(card, fg_color="transparent")
        mode_row.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(mode_row, text="模式", text_color=self.TEXT_COLOR, font=ctk.CTkFont(size=14, weight="bold"), width=78).pack(
            side="left", padx=(0, 10)
        )
        self.target_mode_menu = ctk.CTkOptionMenu(
            mode_row,
            values=list(self.TARGET_MODE_MAP.keys()),
            variable=self.target_mode_var,
            command=self.update_target_mode_ui,
            width=220,
        )
        self.target_mode_menu.pack(side="left")

        self.target_entry = self.create_labeled_entry(card, "目标", self.PLACEHOLDER_MAP["user"])
        self.mode_hint_label = ctk.CTkLabel(card, text="", text_color=self.MUTED_COLOR, font=ctk.CTkFont(size=12), justify="left")
        self.mode_hint_label.pack(anchor="w", padx=20, pady=(0, 10))

        keyword_row = ctk.CTkFrame(card, fg_color="transparent")
        keyword_row.pack(fill="x", padx=20, pady=(0, 12))
        self.keyword_limit_label = ctk.CTkLabel(keyword_row, text="关键词数量", text_color=self.TEXT_COLOR, font=ctk.CTkFont(size=14, weight="bold"), width=78)
        self.keyword_limit_label.pack(
            side="left", padx=(0, 10)
        )
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

        opt = ctk.CTkFrame(card, fg_color="transparent")
        opt.pack(fill="x", padx=20, pady=(0, 14))
        self.use_queue_checkbox = ctk.CTkCheckBox(
            opt,
            text="统一任务队列（SQLite 状态）",
            variable=self.use_queue_var,
            font=ctk.CTkFont(size=13),
            text_color=self.TEXT_COLOR,
        )
        self.use_queue_checkbox.pack(side="left", padx=(0, 18))
        self.incremental_wm_checkbox = ctk.CTkCheckBox(
            opt,
            text="作者增量同步（水印）",
            variable=self.incremental_wm_var,
            font=ctk.CTkFont(size=13),
            text_color=self.TEXT_COLOR,
        )
        self.incremental_wm_checkbox.pack(side="left")

    def create_tips_card(self, parent) -> None:
        card = self.make_card(parent, "使用说明")
        for tip in [
            "Cookie 登录最稳，账号密码登录可能被 Pixiv 额外验证拦截。",
            "“开始爬取并预校验”会把可用代理自动写回代理列表。",
            "关键词图片会先搜索作品列表，再按数量限制批量下载。",
            "开启任务队列后支持多行 / 逗号混合输入与 #标签；作者模式可配合水印只抓新图。",
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

    def set_inputs_state(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.target_entry.configure(state=state)
        self.proxy_button.configure(state=state)
        self.target_mode_menu.configure(state=state)
        self.login_mode_menu.configure(state=state)
        if hasattr(self, "cookie_import_button"):
            self.cookie_import_button.configure(state=state)
        self.login_id_entry.configure(state=state)
        self.password_entry.configure(state=state)
        self.load_session_button.configure(state=state)
        self.validate_session_button.configure(state=state)
        self.login_button.configure(state=state)
        self.save_session_button.configure(state=state)
        self.keyword_limit_entry.configure(
            state=state if self.TARGET_MODE_MAP.get(self.target_mode_var.get(), "user") == "keyword" else "disabled"
        )
        if hasattr(self, "use_queue_checkbox"):
            self.use_queue_checkbox.configure(state=state)
        if hasattr(self, "incremental_wm_checkbox"):
            self.incremental_wm_checkbox.configure(state=state)
        if hasattr(self, "platform_menu"):
            self.platform_menu.configure(state=state)
            if state == "normal":
                self.update_crawler_platform_ui()

    def update_login_mode_ui(self, display_mode: str) -> None:
        mode = self.LOGIN_MODE_MAP.get(display_mode, "cookie")
        self.cookie_frame.pack_forget()
        self.password_frame.pack_forget()
        self.login_button.configure(state="normal" if mode == "password" else "disabled")
        self.validate_session_button.configure(state="normal")
        if mode == "cookie":
            self.cookie_frame.pack(fill="x", pady=(0, 0))
        else:
            self.password_frame.pack(fill="x", pady=(0, 0))

    def update_crawler_platform_ui(self, _choice: str | None = None) -> None:
        platform = self.platform_var.get()
        is_pixiv = platform == "Pixiv"
        if is_pixiv:
            values = list(self.TARGET_MODE_MAP.keys())
        elif platform == "小红书":
            values = list(self.XHS_TARGET_MODE_MAP.keys())
        elif platform == "插件自动识别":
            values = list(self.GENERIC_PLUGIN_MODE_MAP.keys())
        else:
            values = list(self.X_TARGET_MODE_MAP.keys())
        self.target_mode_menu.configure(values=values, state="normal")
        if self.target_mode_var.get() not in values:
            self.target_mode_var.set(values[0])
        self.keyword_row_frame.configure(fg_color="transparent")
        if is_pixiv:
            self.update_target_mode_ui(self.target_mode_var.get())
            self.use_queue_checkbox.configure(state="normal")
            self.incremental_wm_checkbox.configure(state="normal")
        else:
            self.update_target_mode_ui(self.target_mode_var.get())
            self.use_queue_checkbox.configure(state="disabled")
            self.incremental_wm_checkbox.configure(state="disabled")

    def update_target_mode_ui(self, display_mode: str) -> None:
        platform = self.platform_var.get()
        if platform == "插件自动识别":
            self.target_entry.configure(placeholder_text="输入链接、ID 或关键词；程序会自动匹配已导入插件的 can_handle()")
            self.keyword_limit_label.configure(text="抓取数量")
            self.keyword_limit_entry.configure(state="normal")
            self.mode_hint_label.configure(
                text="自动解析：从内置插件和用户导入插件中选择第一个 can_handle() 返回 True 的插件。",
                text_color=self.MUTED_COLOR,
            )
            return
        if platform == "小红书":
            mode = self.XHS_TARGET_MODE_MAP.get(display_mode, "xhs_keyword")
            self.target_entry.configure(placeholder_text=self.XHS_PLACEHOLDER_MAP[mode])
            self.keyword_limit_label.configure(text="抓取数量")
            self.keyword_limit_entry.configure(state="normal" if mode == "xhs_keyword" else "disabled")
            hints = {
                "xhs_keyword": "搜索关键词：解析搜索结果里的笔记封面，下载时会进入笔记抓取图片。",
                "xhs_note": "单篇笔记：解析一条笔记里的图片，请输入完整小红书笔记链接。",
                "xhs_link": "链接解析：支持 xiaohongshu.com 和 xhslink.com 分享链接。",
            }
            self.mode_hint_label.configure(text=hints[mode], text_color=self.MUTED_COLOR)
            return
        if platform != "Pixiv":
            mode = self.X_TARGET_MODE_MAP.get(display_mode, "x_user")
            self.target_entry.configure(placeholder_text=self.X_PLACEHOLDER_MAP[mode])
            self.keyword_limit_label.configure(text="抓取数量")
            self.keyword_limit_entry.configure(state="normal" if mode in {"x_user", "x_keyword"} else "disabled")
            hints = {
                "x_user": "作者媒体：输入作者名会自动访问该作者 /media 页，解析图片和视频。",
                "x_status": "单个推文：解析一条推文里的图片或视频，请输入完整推文链接。",
                "x_keyword": "搜索词媒体：自动打开 X 搜索媒体页，抓取搜索结果里的图片或视频。",
            }
            self.mode_hint_label.configure(text=hints[mode], text_color=self.MUTED_COLOR)
            return
        mode = self.TARGET_MODE_MAP.get(display_mode, "user")
        self.keyword_limit_label.configure(text="关键词数量")
        self.target_entry.configure(placeholder_text=self.PLACEHOLDER_MAP[mode])
        self.keyword_limit_entry.configure(state="normal" if mode == "keyword" else "disabled")
        hints = {
            "user": "作者全部作品：支持作者 ID，也支持先粘贴任意作品链接自动反查作者并批量下载。",
            "illust": "单个作品：只抓取这一条作品的原图，多图会全部下载。",
            "novel": "文章/小说：保存为 TXT 文件，方便后续整理和导出。",
            "keyword": "关键词图片：按搜索结果抓图，数量由“关键词数量”控制。",
        }
        self.mode_hint_label.configure(text=hints[mode])

    def get_active_cookie(self) -> str:
        payload = self.session_store.load()
        cookies = self.cookie_json or payload.get("cookie_json") or []
        if cookies:
            try:
                return cookies_to_header(list(cookies), ("pixiv.net",))
            except Exception:
                pass
        if self.cookie_header:
            return self.cookie_header
        return str(payload.get("cookie", "")).strip()

    def save_session(self) -> None:
        cookie = self.get_active_cookie()
        if not cookie and not self.cookie_json:
            messagebox.showerror("保存失败", "当前没有可保存的 Cookie。")
            return
        payload = {
            "login_mode": self.LOGIN_MODE_MAP.get(self.login_mode_var.get(), "cookie"),
            "cookie": cookie,
            "cookie_json": self.cookie_json,
            "login_id": self.login_id_entry.get().strip(),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.session_store.save(payload)
        self.auth_status_var.set("会话已保存，下次启动可自动载入。")
        self.log_message("已保存本地会话。")

    def set_cookie_indicator(self, text: str) -> None:
        self.cookie_import_button.configure(text=text)

    def show_login_warning(self) -> None:
        if getattr(self, "_login_notice_window", None) is not None:
            try:
                self._login_notice_window.focus()
                return
            except Exception:
                self._login_notice_window = None

        pages = [
            (
                "【登录功能说明】",
                [
                    "当前登录功能仍在优化中，建议使用 cookie 导入方式进行登录。",
                    "推荐使用浏览器插件（如 Get cookies.txt）导出登录态，并导入本工具使用。",
                ],
            ),
            (
              "【小红书插件说明】",
                ["当前小红书插件有些许问题，模拟爬取小红书时，会404 NOT FOUND",
                 "作者会持续优化，还请耐心等待....."],
            ),
            (
                "【使用建议】",
                [
                    "1. 建议在已正常登录 Pixiv / X（Twitter）的浏览器环境下获取 cookie",
                    "2. 保持浏览器在后台请勿关闭",
                    "3. cookie 仅用于维持登录状态，请定期更新（例如失效时重新导入）",
                    "4. 请避免在短时间内进行高频或大规模下载操作，以降低触发平台限制的风险",
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
                    "本工具只做研究学习使用，严谨非法搬运",
                    "若遇到问题，请联系作者",
                    "QQ:2811043066",
                    "使用方法请前往博客",
                    "博客:http://blog.kunkunxiaomao.top",
                ]
            )
        ]

        notice_width = 680
        notice_height = 520

        win = ctk.CTkToplevel(self.root)
        self._login_notice_window = win
        win.title("用户须知")
        win.geometry(f"{notice_width}x{notice_height}")
        win.minsize(620, 480)
        win.configure(fg_color=self.BG_COLOR)
        win.transient(self.root)
        win.grab_set()

        def close_notice() -> None:
            self._login_notice_window = None
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

        pixiv_cookies = cookies_to_playwright(cookies, ("pixiv.net",)) if has_cookie_domain(cookies, ("pixiv.net",)) else []
        x_cookies = cookies_to_playwright(cookies, ("x.com", "twitter.com")) if has_cookie_domain(cookies, ("x.com", "twitter.com")) else []
        xhs_cookies = (
            cookies_to_playwright(cookies, ("xiaohongshu.com", "xhslink.com"))
            if has_cookie_domain(cookies, ("xiaohongshu.com", "xhslink.com"))
            else []
        )

        self.cookie_json = cookies
        self.cookie_header = cookies_to_header(pixiv_cookies, ("pixiv.net",)) if pixiv_cookies else ""
        self.login_mode_var.set("Cookie 登录")
        self.update_login_mode_ui("Cookie 登录")
        summary = cookie_summary(cookies)
        if x_cookies:
            save_playwright_cookies(x_cookies, runtime_path("x_cookies.json"))
        if xhs_cookies:
            save_playwright_cookies(xhs_cookies, runtime_path("xiaohongshu_cookies.json"))
        self.set_cookie_indicator(f"已导入 {Path(path).name}（{len(cookies)} 个 Cookie）")
        self.save_session()
        if pixiv_cookies:
            self.auth_status_var.set(f"已从 txt 导入 Pixiv Cookie：{summary}")
        elif x_cookies:
            self.auth_status_var.set(f"已从 txt 导入 X Cookie：{summary}；已同步到 runtime/x_cookies.json")
        elif xhs_cookies:
            self.auth_status_var.set(f"已从 txt 导入小红书 Cookie：{summary}；已同步到 runtime/xiaohongshu_cookies.json")
        else:
            self.auth_status_var.set(f"已从 txt 导入 Cookie：{summary}")
        self.log_message(f"已导入 Cookie txt: {path} ({summary})")
        domain_note = ", ".join(cookie_domains(cookies)[:4])
        extras = []
        if x_cookies:
            extras.append("X Cookie 到 runtime/x_cookies.json")
        if xhs_cookies:
            extras.append("小红书 Cookie 到 runtime/xiaohongshu_cookies.json")
        extra = "，并已同步 " + "；".join(extras) if extras else ""
        messagebox.showinfo("导入完成", f"已解析为 JSON 并保存 Cookie。\n{summary}{extra}\n{domain_note}")

    def load_saved_session(self, silent: bool = False) -> None:
        payload = self.session_store.load()
        if not payload:
            if not silent:
                messagebox.showinfo("提示", "本地还没有保存的会话。")
            return
        login_mode = payload.get("login_mode", "cookie")
        display_mode = "账号密码登录" if login_mode == "password" else "Cookie 登录"
        self.login_mode_var.set(display_mode)
        self.update_login_mode_ui(display_mode)
        self.cookie_json = list(payload.get("cookie_json") or [])
        self.cookie_header = str(payload.get("cookie", "") or "") if not self.cookie_json else ""
        if self.cookie_json:
            self.set_cookie_indicator(f"已载入 JSON Cookie（{len(self.cookie_json)} 个）")
        elif payload.get("cookie"):
            self.set_cookie_indicator("已载入旧版 Cookie header")
        else:
            self.set_cookie_indicator("导入 cookies.txt / json")
        self.login_id_entry.delete(0, "end")
        self.login_id_entry.insert(0, str(payload.get("login_id", "")))
        self.auth_status_var.set(f"已载入本地会话，保存时间：{payload.get('saved_at', '未知')}")
        self.log_message("已自动载入本地会话。")

    def _run_auth_thread(self, worker) -> None:
        threading.Thread(target=worker, daemon=True).start()

    def validate_session(self) -> None:
        cookie = self.get_active_cookie()
        if not cookie:
            messagebox.showerror("检测失败", "当前没有 Cookie 可检测。")
            return
        self.auth_status_var.set("正在检测当前会话...")

        def worker() -> None:
            result = self.auth_client.validate_cookie(cookie)
            self.enqueue_ui(self.apply_auth_result, result, False)

        self._run_auth_thread(worker)

    def login_with_password(self) -> None:
        login_id = self.login_id_entry.get().strip()
        password = self.password_entry.get().strip()
        if not login_id or not password:
            messagebox.showerror("登录失败", "请先填写账号和密码。")
            return
        self.auth_status_var.set("正在尝试账号密码登录...")

        def worker() -> None:
            result = self.auth_client.login_with_password(login_id, password)
            self.enqueue_ui(self.apply_auth_result, result, True)

        self._run_auth_thread(worker)

    def apply_auth_result(self, result: AuthResult, from_password_login: bool) -> None:
        self.auth_status_var.set(result.message)
        self.log_message(result.message)
        if result.success and result.cookie:
            self.cookie_json = []
            self.cookie_header = result.cookie
            self.set_cookie_indicator("账号密码登录已生成 Cookie header")
            if from_password_login:
                self.login_mode_var.set("Cookie 登录")
                self.update_login_mode_ui("Cookie 登录")
            self.save_session()
        elif from_password_login and result.requires_verification:
            messagebox.showinfo("需要额外验证", result.message)
        elif not result.success and not from_password_login:
            messagebox.showerror("检测失败", result.message)

    def open_plugin_panel(self) -> None:
        from pixiv_app.gui.plugin_panel import PluginPanelWindow

        PluginPanelWindow(self.root, cookie=self.get_active_cookie())

    def open_proxy_dialog(self) -> None:
        ProxyDialog(self.root, self.proxy_config, self.save_proxy_config, self.handle_proxy_dialog_action)

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

    def collect_job_config(self) -> dict:
        return {
            "mode": self.TARGET_MODE_MAP.get(self.target_mode_var.get(), "user"),
            "target": self.target_entry.get().strip(),
            "cookie": self.get_active_cookie(),
            "keyword_limit": self.keyword_limit_entry.get().strip() or "20",
            "use_queue": self.use_queue_var.get(),
            "incremental_wm": self.incremental_wm_var.get(),
        }

    def validate_job_config(self, config: dict) -> dict:
        if self.use_queue_var.get():
            return self._validate_job_config_queue(config)
        return self._validate_job_config_legacy(config)

    def _validate_job_config_queue(self, config: dict) -> dict:
        mode = config["mode"]
        target = config["target"].strip()
        if not target:
            raise ValueError("请输入抓取目标。")
        if not str(config["keyword_limit"]).isdigit():
            raise ValueError("关键词数量必须是正整数。")
        config["keyword_limit"] = max(1, min(int(config["keyword_limit"]), 100))
        lines, _preview = parse_batch_for_mode(target, ui_mode=mode)
        if mode == "keyword":
            if not lines:
                raise ValueError("请输入关键词（可与 # 标签形式混排）。")
            return config
        if not lines:
            raise ValueError("未解析到有效目标，请检查链接、纯数字 ID 或 #标签。")
        if mode == "user":
            for line in lines:
                if line.category == "novel":
                    raise ValueError("作者全部作品模式中出现小说链接，请改用文章/小说模式或删除该条目。")
        if mode == "illust":
            for line in lines:
                if line.category != "illust":
                    raise ValueError("单个作品模式下仅支持插画作品 ID、链接或未知数字 ID。")
        if mode == "novel":
            for line in lines:
                if line.category != "novel":
                    raise ValueError("文章/小说模式下请输入小说 ID、链接或未知数字 ID。")
        return config

    def _validate_job_config_legacy(self, config: dict) -> dict:
        mode = config["mode"]
        target = config["target"]
        if not target:
            raise ValueError("请输入抓取目标。")
        if mode == "keyword":
            if not str(config["keyword_limit"]).isdigit():
                raise ValueError("关键词数量必须是正整数。")
            config["keyword_limit"] = max(1, min(int(config["keyword_limit"]), 100))
            return config
        target_kind, target_id = parse_pixiv_target(target)
        config["target_kind"] = target_kind
        config["target_id"] = target_id
        if mode == "user" and target_kind == "novel":
            raise ValueError("作者全部作品模式不接受小说链接，请输入作者 ID 或作品链接。")
        if mode == "illust" and target_kind not in {"illust", "unknown"}:
            raise ValueError("当前模式是单个作品，请输入作品 ID 或作品链接。")
        if mode == "novel" and target_kind in {"user", "illust"}:
            raise ValueError("当前模式是文章/小说，请输入小说 ID 或小说链接。")
        return config

    def _plugins_root(self) -> Path:
        return plugins_root()

    def _plugin_roots(self) -> list[Path]:
        return plugin_roots()

    def _plugin_manager(self) -> PluginManager:
        if getattr(self, "_cached_plugin_manager", None) is None:
            self._cached_plugin_manager = PluginManager(self._plugin_roots())
            self._cached_plugin_manager.load_all()
        return self._cached_plugin_manager

    def _validate_x_url(self) -> None:
        text = self.target_entry.get().strip()
        if not text:
            raise ValueError("请输入 X / Twitter 抓取目标。")
        mode = self.X_TARGET_MODE_MAP.get(self.target_mode_var.get(), "x_user")
        normalized = self._normalize_x_target(text, mode=mode)
        if normalized:
            if mode == "x_status" and "/status/" not in normalized.lower():
                raise ValueError("单个推文模式请输入完整推文链接。")
            return
        x_plugin = self._plugin_manager().plugins.get("X")
        if x_plugin is not None and not x_plugin.can_handle(text):
            raise ValueError(
                "目标格式不符合当前 X 模式；作者模式可填作者名，单个模式填推文链接，搜索模式填关键词。"
            )

    def _normalize_x_target(self, text: str, *, mode: str | None = None) -> str:
        mode = mode or self.X_TARGET_MODE_MAP.get(self.target_mode_var.get(), "x_user")
        value = text.strip()
        if not value:
            return ""
        lower = value.lower()
        if mode == "x_keyword":
            return f"https://x.com/search?q={quote(value)}&src=typed_query&f=media"
        if mode == "x_status":
            return value if ("twitter.com/" in lower or "x.com/" in lower) and "/status/" in lower else ""
        if "twitter.com/" in lower or "x.com/" in lower:
            if "/status/" in lower:
                return ""
            if lower.rstrip("/").endswith("/media"):
                return value
            return value.rstrip("/") + "/media"
        if "://" in value:
            return value
        if value.startswith("@"):
            value = value[1:]
        if len(value) <= 15 and value.replace("_", "").isalnum():
            return f"https://x.com/{value}/media"
        return ""

    def _x_max_items(self) -> int:
        value = self.keyword_limit_entry.get().strip() or "20"
        if not value.isdigit():
            raise ValueError("抓取数量必须是正整数。")
        return max(1, min(int(value), 120))

    def _validate_xhs_target(self) -> str:
        text = self.target_entry.get().strip()
        if not text:
            raise ValueError("请输入小红书抓取目标。")
        mode = self.XHS_TARGET_MODE_MAP.get(self.target_mode_var.get(), "xhs_keyword")
        lower = text.lower()
        if mode == "xhs_keyword":
            if "xiaohongshu.com" in lower or "xhslink.com" in lower:
                raise ValueError("搜索关键词模式请输入关键词；小红书链接请切换到“单篇笔记”或“链接解析”。")
            return text
        if mode == "xhs_note":
            if "xiaohongshu.com/explore/" not in lower and "xhslink.com" not in lower and "note_id" not in lower:
                raise ValueError("单篇笔记模式请输入小红书笔记链接或 xhslink.com 分享链接。")
            return text
        if "xiaohongshu.com" not in lower and "xhslink.com" not in lower:
            raise ValueError("链接解析模式请输入 xiaohongshu.com 或 xhslink.com 链接。")
        return text

    def _xhs_max_items(self) -> int:
        value = self.keyword_limit_entry.get().strip() or "20"
        if not value.isdigit():
            raise ValueError("抓取数量必须是正整数。")
        return max(1, min(int(value), 50))

    def _plugin_max_items(self) -> int:
        value = self.keyword_limit_entry.get().strip() or "20"
        if not value.isdigit():
            raise ValueError("抓取数量必须是正整数。")
        return max(1, min(int(value), 200))

    def start_download(self) -> None:
        if self.is_downloading:
            return
        if self.platform_var.get() == "插件自动识别":
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
            self.log_message(f"准备开始：平台=插件自动识别，目标={target[:120]}")
            self.download_thread = threading.Thread(
                target=self.run_download_generic_plugin_thread,
                args=(target, max_items),
                daemon=True,
            )
            self.download_thread.start()
            return
        if self.platform_var.get() == "小红书":
            try:
                target = self._validate_xhs_target()
                max_items = self._xhs_max_items()
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
            self.progress_label.configure(text="正在准备小红书任务...")
            self.log_message(f"准备开始：平台=小红书，模式={self.target_mode_var.get()}，目标={target[:120]}")
            self.download_thread = threading.Thread(
                target=self.run_download_xiaohongshu_thread,
                args=(target, max_items),
                daemon=True,
            )
            self.download_thread.start()
            return
        if self.platform_var.get() != "Pixiv":
            try:
                self._validate_x_url()
            except ValueError as exc:
                messagebox.showerror("输入错误", str(exc))
                return
            raw_target = self.target_entry.get().strip()
            x_mode = self.X_TARGET_MODE_MAP.get(self.target_mode_var.get(), "x_user")
            url = self._normalize_x_target(raw_target, mode=x_mode) or raw_target
            try:
                max_items = self._x_max_items()
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
            self.progress_label.configure(text="正在准备 X 任务...")
            self.log_message(f"准备开始：平台=X / Twitter，模式={self.target_mode_var.get()}，目标={url[:120]}")
            self.download_thread = threading.Thread(target=self.run_download_x_thread, args=(url, max_items), daemon=True)
            self.download_thread.start()
            return

        try:
            config = self.validate_job_config(self.collect_job_config())
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
        self.progress_label.configure(text="正在准备任务...")
        self.log_message(f"准备开始：模式={self.target_mode_var.get()}，目标={config['target']}")
        self.download_thread = threading.Thread(target=self.run_download, args=(config,), daemon=True)
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

    def prepare_proxy_pool(self) -> ProxyPool | None:
        manual_text = self.proxy_config.get("manual_text", "")
        use_quake = bool(self.proxy_config.get("use_quake"))
        countries_text = str(self.proxy_config.get("countries_text", "")).strip()
        countries = [item.strip().upper() for item in countries_text.split(",") if item.strip()]
        pool = ProxyPool()
        manual_proxies = parse_proxy_text(manual_text)
        if manual_proxies:
            pool.add_proxies(manual_proxies)
            pool.verify_all(max_workers=12, max_proxies=80)
            self.enqueue_ui(self.log_message, f"手动代理校验完成: 可用 {len(pool.working_proxies)}/{len(manual_proxies)}")
        if use_quake:
            quake = QuakeClient(
                api_key=self.proxy_config.get("quake_api_key", ""),
                cookie=self.proxy_config.get("quake_cookie", ""),
                mode=self.proxy_config.get("quake_mode", "api_v3"),
            )
            pool.collect_until_target(
                quake_client=quake,
                target_working=5,
                stable_rounds=2,
                fetch_batch_size=60,
                verify_batch_size=40,
                max_rounds=6,
                max_workers=12,
                countries=countries or None,
            )
            self.enqueue_ui(self.log_message, f"Quake 抓取并校验后可用代理: {len(pool.working_proxies)}")
        if not pool.working_proxies:
            self.enqueue_ui(self.log_message, "未获得可用代理，将回退到直连模式。")
            return None
        return pool

    def run_download(self, config: dict) -> None:
        try:
            if config.get("use_queue", True):
                self.run_download_queue(config)
            else:
                self.run_download_legacy(config)
        except Exception as exc:
            self.enqueue_ui(self.handle_error, f"程序运行出错: {exc}")

    def run_download_x_thread(self, url: str, max_items: int = 20) -> None:
        """Parse and download via bundled X plugin (Playwright); not using Pixiv SQLite task queue."""
        try:
            plugin = self._plugin_manager().plugins.get("X")
            if plugin is None or not plugin.validate():
                self.enqueue_ui(
                    self.handle_error,
                    "X 插件不可用：请 pip install playwright httpx && playwright install chromium，"
                    "并确认 plugins/x 存在后重启应用或稍后在插件管理中重载。",
                )
                return

            self.enqueue_ui(self.log_message, "正在解析 X 页面…")
            if hasattr(plugin, "config"):
                plugin.config.max_media_items = max_items
            resources = plugin.parse(url)
            resources = resources[:max_items]
            if self.stop_event.is_set():
                self.enqueue_ui(self.handle_finish, True)
                return
            if not resources:
                self.enqueue_ui(
                    self.handle_error,
                    "解析结果为空（无媒体、需登录，或 X 临时风控/限流）。请等待 1-3 分钟后重试，"
                    "必要时重新导入 x.com_cookies.txt。",
                )
                return

            total = len(resources)
            self.enqueue_ui(self.set_total_works, total)
            save_root = downloads_root() / "x"
            completed = 0
            for i, res in enumerate(resources):
                if self.stop_event.is_set():
                    break
                try:
                    paths = plugin.download(res, save_root)
                    ok = len(paths) > 0
                    pages = len(res.files)
                    downloaded = len(paths)
                    failed_p = 0 if ok else max(1, pages)
                    msg = f"[X] {res.title[:60]} — 保存文件 {downloaded}/{pages}"
                    result = DownloadResult(
                        work_id=i,
                        total_pages=max(pages, 1),
                        downloaded_pages=downloaded if ok else 0,
                        skipped_pages=0,
                        failed_pages=failed_p if not ok else 0,
                        ok=ok,
                        message=msg,
                    )
                except Exception as exc:
                    result = DownloadResult(
                        work_id=i,
                        total_pages=1,
                        downloaded_pages=0,
                        skipped_pages=0,
                        failed_pages=1,
                        ok=False,
                        message=f"[X] 下载失败: {exc}",
                    )
                completed += 1
                self.enqueue_ui(self.handle_result, result, completed, total)

            self.enqueue_ui(self.handle_finish, self.stop_event.is_set())
        except Exception as exc:
            self.enqueue_ui(self.handle_error, f"X 下载出错: {exc}")

    def run_download_xiaohongshu_thread(self, target: str, max_items: int = 20) -> None:
        """Parse and download via Xiaohongshu plugin (Playwright)."""
        try:
            plugin = self._plugin_manager().plugins.get("小红书")
            if plugin is None:
                self.enqueue_ui(self.handle_error, "小红书插件不可用：未找到 plugins/xiaohongshu。")
                return
            if not plugin.validate():
                self.enqueue_ui(
                    self.handle_error,
                    "小红书插件依赖不可用：请 pip install playwright requests && playwright install chromium。",
                )
                return

            if hasattr(plugin, "config"):
                plugin.config.max_notes_per_session = max_items

            progress_state = {"base": 0.02, "span": 0.28}

            def on_xhs_progress(message: str, value: float) -> None:
                progress = progress_state["base"] + max(0.0, min(1.0, float(value))) * progress_state["span"]
                self.enqueue_ui(self.update_stage_progress, message, progress)

            if hasattr(plugin, "progress_callback"):
                plugin.progress_callback = on_xhs_progress

            self.enqueue_ui(self.update_stage_progress, "小红书：准备解析", 0.02)
            self.enqueue_ui(self.log_message, "正在解析小红书页面…")
            resources = plugin.parse(target)
            resources = resources[:max_items]
            if self.stop_event.is_set():
                self.enqueue_ui(self.handle_finish, True)
                return
            if not resources:
                self.enqueue_ui(
                    self.handle_error,
                    "解析结果为空（无图片、需登录，或页面暂时不可访问）。请确认已导入小红书 Cookie 后再试。",
                )
                return

            total = len(resources)
            self.enqueue_ui(self.set_total_works, total)
            self.enqueue_ui(self.update_stage_progress, f"小红书：解析到 {total} 个资源，准备下载", 0.30)
            save_root = downloads_root()
            completed = 0
            for i, res in enumerate(resources):
                if self.stop_event.is_set():
                    break
                progress_state["base"] = 0.30 + (i / total) * 0.68
                progress_state["span"] = 0.68 / total
                self.enqueue_ui(self.update_stage_progress, f"小红书：下载第 {i + 1}/{total} 个资源", progress_state["base"])
                try:
                    paths = plugin.download(res, save_root)
                    ok = len(paths) > 0
                    pages = len(res.files) if res.files else max(len(paths), 1)
                    downloaded = len(paths)
                    failed_p = 0 if ok else max(1, pages)
                    title = res.title or res.id or f"项目 {i + 1}"
                    result = DownloadResult(
                        work_id=i,
                        total_pages=max(pages, 1),
                        downloaded_pages=downloaded if ok else 0,
                        skipped_pages=0,
                        failed_pages=failed_p if not ok else 0,
                        ok=ok,
                        message=f"[小红书] {title[:60]} — 保存文件 {downloaded}/{max(pages, 1)}",
                    )
                except Exception as exc:
                    result = DownloadResult(
                        work_id=i,
                        total_pages=1,
                        downloaded_pages=0,
                        skipped_pages=0,
                        failed_pages=1,
                        ok=False,
                        message=f"[小红书] 下载失败: {exc}",
                    )
                completed += 1
                self.enqueue_ui(self.handle_result, result, completed, total)

            self.enqueue_ui(self.update_stage_progress, "小红书：任务收尾中", 0.98)
            self.enqueue_ui(self.handle_finish, self.stop_event.is_set())
        except Exception as exc:
            self.enqueue_ui(self.handle_error, f"小红书下载出错: {exc}")
        finally:
            try:
                if "plugin" in locals() and hasattr(plugin, "progress_callback"):
                    plugin.progress_callback = None
            except Exception:
                pass

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

    def run_download_queue(self, config: dict) -> None:
        requested_workers = int(self.thread_slider.get())
        request_delay = float(self.delay_slider.get())
        proxy_pool = self.prepare_proxy_pool() if self.proxy_config.get("enabled") else None
        max_workers = resolve_workers(requested_workers, proxy_mode=proxy_pool is not None)
        if proxy_pool is not None:
            self.enqueue_ui(self.log_message, f"代理模式启用，线程数自动调整为 {max_workers}")

        cookie = config["cookie"]

        def on_progress(result: DownloadResult, completed: int, total: int) -> None:
            self.enqueue_ui(self.handle_result, result, completed, total)

        specs, preview, summary = collect_task_specs(
            config=config,
            cookie=cookie,
            proxy_pool=proxy_pool,
            incremental_watermark=config.get("incremental_wm", True),
            stop_event=self.stop_event,
        )
        self.enqueue_ui(self.log_message, summary)
        stat_txt = ", ".join(f"{k}:{v}" for k, v in sorted(preview.by_category.items())) or "无"
        self.enqueue_ui(self.log_message, f"解析预览: {stat_txt}（输入条数 {preview.total_lines}）")
        if not specs:
            self.enqueue_ui(self.handle_error, "没有生成下载任务（列表为空或被水印过滤）。")
            return

        inserted, skipped, requeued_failed, incomplete_left = enqueue_specs_to_library(specs)
        self.enqueue_ui(
            self.log_message,
            f"任务队列: 新插入 {inserted}，指纹跳过 {skipped}，失败重排队 {requeued_failed}，"
            f"本批未完成 {incomplete_left}",
        )
        if incomplete_left == 0 and inserted == 0 and requeued_failed == 0:
            self.enqueue_ui(
                self.handle_error,
                "本批链接对应的任务均已完成（download_tasks 中为已完成状态）。"
                "若要强制重新下载，需先在库中清理对应记录或扩展任务模型。",
            )
            return

        if inserted == 0 and (incomplete_left > 0 or requeued_failed > 0):
            self.enqueue_ui(
                self.log_message,
                "指纹已在队列中；将继续执行未完成 / 失败的任务（无需重复插入）。",
            )

        self.enqueue_ui(self.set_total_works, incomplete_left)

        run_queue_until_idle(
            cookie=cookie,
            proxy_pool=proxy_pool,
            max_workers=max_workers,
            request_delay=request_delay,
            stop_event=self.stop_event,
            progress_callback=on_progress,
            poll_ui=None,
            progress_total=incomplete_left,
        )
        self.enqueue_ui(self.handle_finish, self.stop_event.is_set())

    def run_download_legacy(self, config: dict) -> None:
        try:
            requested_workers = int(self.thread_slider.get())
            request_delay = float(self.delay_slider.get())
            proxy_pool = self.prepare_proxy_pool() if self.proxy_config.get("enabled") else None
            max_workers = resolve_workers(requested_workers, proxy_mode=proxy_pool is not None)
            if proxy_pool is not None:
                self.enqueue_ui(self.log_message, f"代理模式启用，线程数自动调整为 {max_workers}")

            mode = config["mode"]
            cookie = config["cookie"]

            def on_progress(result: DownloadResult, completed: int, total: int) -> None:
                self.enqueue_ui(self.handle_result, result, completed, total)

            if mode == "user":
                target_kind = config["target_kind"]
                target_id = config["target_id"]
                kind_text = {"user": "作者 ID", "illust": "作品 ID", "unknown": "数字 ID", "novel": "小说 ID"}
                self.enqueue_ui(self.log_message, f"已识别为{kind_text.get(target_kind, '目标')}: {target_id}")
                resolved_user_id, work_ids = fetch_user_work_ids(user_id=target_id, cookie=cookie, proxy_pool=proxy_pool, input_kind=target_kind)
                if resolved_user_id != target_id or target_kind == "illust":
                    self.enqueue_ui(self.log_message, f"已自动解析到作者 ID: {resolved_user_id}")
                self.enqueue_ui(self.set_total_works, len(work_ids))
                if not work_ids:
                    self.enqueue_ui(self.handle_error, "没有获取到可下载的作品。请确认 ID 正确，或尝试提供可用会话。")
                    return
                self.enqueue_ui(self.log_message, f"已获取 {len(work_ids)} 个作品，开始下载。")
                download_user_works(
                    user_id=resolved_user_id,
                    cookie=cookie,
                    max_workers=max_workers,
                    request_delay=request_delay,
                    stop_event=self.stop_event,
                    progress_callback=on_progress,
                    work_ids=work_ids,
                    proxy_pool=proxy_pool,
                )
            elif mode == "illust":
                target_id = config["target_id"]
                self.enqueue_ui(self.set_total_works, 1)
                result = download_illust(
                    illust_id=target_id,
                    user_id=0,
                    cookie=cookie,
                    save_subdir=str(Path("single_illust") / str(target_id)),
                    request_delay=request_delay,
                    stop_event=self.stop_event,
                    proxy_pool=proxy_pool,
                )
                self.enqueue_ui(self.handle_result, result, 1, 1)
            elif mode == "novel":
                target_id = config["target_id"]
                self.enqueue_ui(self.set_total_works, 1)
                result = download_novel(
                    novel_id=target_id,
                    cookie=cookie,
                    save_subdir="novels",
                    request_delay=request_delay,
                    stop_event=self.stop_event,
                    proxy_pool=proxy_pool,
                )
                self.enqueue_ui(self.handle_result, result, 1, 1)
            elif mode == "keyword":
                keyword = config["target"]
                limit = config["keyword_limit"]
                preview_ids = search_illust_ids_by_keyword(keyword, cookie=cookie, proxy_pool=proxy_pool, limit=limit)
                self.enqueue_ui(self.set_total_works, len(preview_ids))
                if not preview_ids:
                    self.enqueue_ui(self.handle_error, f"关键词“{keyword}”没有搜索到可下载作品。")
                    return
                self.enqueue_ui(self.log_message, f"关键词“{keyword}”搜索到 {len(preview_ids)} 个作品，开始下载。")
                download_keyword_works(
                    keyword,
                    cookie=cookie,
                    max_workers=max_workers,
                    request_delay=request_delay,
                    stop_event=self.stop_event,
                    progress_callback=on_progress,
                    proxy_pool=proxy_pool,
                    limit=limit,
                    work_ids=preview_ids,
                )
            else:
                raise PixivRequestError(f"不支持的模式: {mode}")

            self.enqueue_ui(self.handle_finish, self.stop_event.is_set())
        except Exception as exc:
            self.enqueue_ui(self.handle_error, f"程序运行出错: {exc}")

    def set_total_works(self, total: int) -> None:
        self.total_works = total
        self.refresh_overview()
        self.progress_label.configure(text="正在抓取...")

    def run(self) -> None:
        self.root.mainloop()
