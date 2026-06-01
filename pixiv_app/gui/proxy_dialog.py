from __future__ import annotations

import threading
from typing import Any, Callable, Optional

import customtkinter as ctk


class ProxyDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        initial_config: dict,
        on_save: Callable[[dict], None],
        on_action: Optional[Callable[[str, dict], str | dict[str, Any]]] = None,
    ):
        super().__init__(master)
        self.title("代理池设置")
        self.geometry("760x700")
        self.minsize(700, 640)
        self.on_save = on_save
        self.on_action = on_action
        self.initial_config = initial_config

        self.use_proxy_var = ctk.BooleanVar(value=bool(initial_config.get("enabled", False)))
        self.use_quake_var = ctk.BooleanVar(value=bool(initial_config.get("use_quake", False)))
        self.quake_mode_var = ctk.StringVar(value=str(initial_config.get("quake_mode", "api_v3")))
        self.status_var = ctk.StringVar(value="准备就绪")

        self._build_ui()
        self.transient(master)
        self.grab_set()

    def _build_ui(self) -> None:
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(container, text="代理池设置", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", pady=(0, 6))
        ctk.CTkLabel(
            container,
            text="预校验通过的代理会自动回填到下面的代理列表里，保存后下次可直接使用。",
            text_color="#6f8dac",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(0, 12))

        toggle_row = ctk.CTkFrame(container, fg_color="#f7fbff", corner_radius=12)
        toggle_row.pack(fill="x", pady=(0, 10), padx=2)
        ctk.CTkSwitch(toggle_row, text="启用代理池", variable=self.use_proxy_var).pack(anchor="w", padx=12, pady=(8, 6))
        ctk.CTkSwitch(toggle_row, text="启用 Quake 自动爬取", variable=self.use_quake_var).pack(anchor="w", padx=12, pady=(0, 10))

        mode_row = ctk.CTkFrame(container, fg_color="transparent")
        mode_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(mode_row, text="Quake 模式:", width=120).pack(side="left")
        ctk.CTkOptionMenu(mode_row, values=["api_v3", "web_assoc"], variable=self.quake_mode_var, width=220).pack(side="left")

        key_row = ctk.CTkFrame(container, fg_color="transparent")
        key_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(key_row, text="Quake API Key:", width=120).pack(side="left")
        self.api_key_entry = ctk.CTkEntry(key_row, placeholder_text="输入 Quake API Key")
        self.api_key_entry.pack(side="left", fill="x", expand=True)
        self.api_key_entry.insert(0, str(self.initial_config.get("quake_api_key", "")))

        cookie_row = ctk.CTkFrame(container, fg_color="transparent")
        cookie_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(cookie_row, text="Quake Cookie:", width=120).pack(side="left")
        self.cookie_entry = ctk.CTkEntry(cookie_row, placeholder_text="输入 Quake Cookie（web_assoc 模式需要）")
        self.cookie_entry.pack(side="left", fill="x", expand=True)
        self.cookie_entry.insert(0, str(self.initial_config.get("quake_cookie", "")))

        country_row = ctk.CTkFrame(container, fg_color="transparent")
        country_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(country_row, text="海外国家代码:", width=120).pack(side="left")
        self.country_entry = ctk.CTkEntry(country_row, placeholder_text="US,JP,KR,SG,HK,TW,DE,FR,GB,CA,AU")
        self.country_entry.pack(side="left", fill="x", expand=True)
        self.country_entry.insert(0, str(self.initial_config.get("countries_text", "US,JP,KR,SG,HK,TW,DE,FR,GB,CA,AU")))

        tool_row = ctk.CTkFrame(container, fg_color="transparent")
        tool_row.pack(fill="x", pady=(8, 6))
        self.manual_input = ctk.CTkEntry(tool_row, placeholder_text="快速添加单个代理，如 1.2.3.4:8080")
        self.manual_input.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(tool_row, text="手动添加", width=110, command=self._add_manual_proxy).pack(side="left", padx=(8, 0))
        self.fetch_button = ctk.CTkButton(tool_row, text="开始爬取并预校验", width=170, command=self._trigger_fetch)
        self.fetch_button.pack(side="left", padx=(8, 0))

        ctk.CTkLabel(
            container,
            text="代理列表（每行一个，支持 ip:port / http://ip:port / https://ip:port）",
            anchor="w",
        ).pack(fill="x", pady=(8, 0))
        self.proxy_text = ctk.CTkTextbox(container, height=360)
        self.proxy_text.pack(fill="both", expand=True, pady=(6, 12))
        self.proxy_text.insert("1.0", str(self.initial_config.get("manual_text", "")))

        status_row = ctk.CTkFrame(container, fg_color="#f7fbff", corner_radius=12)
        status_row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(status_row, textvariable=self.status_var, text_color="#365b85", font=ctk.CTkFont(size=12)).pack(
            anchor="w", padx=12, pady=8
        )

        button_row = ctk.CTkFrame(container, fg_color="transparent")
        button_row.pack(fill="x")
        ctk.CTkButton(button_row, text="取消", command=self.destroy, width=120).pack(side="right", padx=(8, 0))
        ctk.CTkButton(button_row, text="保存", command=self._save, width=120).pack(side="right")

    def _collect_config(self) -> dict:
        return {
            "enabled": bool(self.use_proxy_var.get()),
            "use_quake": bool(self.use_quake_var.get()),
            "quake_mode": self.quake_mode_var.get().strip() or "api_v3",
            "quake_api_key": self.api_key_entry.get().strip(),
            "quake_cookie": self.cookie_entry.get().strip(),
            "manual_text": self.proxy_text.get("1.0", "end").strip(),
            "countries_text": self.country_entry.get().strip(),
        }

    def _add_manual_proxy(self) -> None:
        value = self.manual_input.get().strip()
        if not value:
            self.status_var.set("请输入代理地址后再添加。")
            return
        old_text = self.proxy_text.get("1.0", "end").strip()
        new_text = f"{old_text}\n{value}".strip()
        self.proxy_text.delete("1.0", "end")
        self.proxy_text.insert("1.0", new_text)
        self.manual_input.delete(0, "end")
        self.status_var.set("已加入手动代理列表。")

    def _trigger_fetch(self) -> None:
        if not self.on_action:
            self.status_var.set("当前版本未启用预校验动作。")
            return
        self.fetch_button.configure(state="disabled", text="处理中...")
        self.status_var.set("正在爬取并校验代理，请稍候...")

        config = self._collect_config()

        def worker() -> None:
            try:
                result = self.on_action("fetch_preview", config)
            except Exception as exc:
                result = {"status": f"预校验失败: {exc}"}
            self.after(0, lambda: self._finish_fetch(result))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_fetch(self, result: str | dict[str, Any]) -> None:
        self.fetch_button.configure(state="normal", text="开始爬取并预校验")
        if isinstance(result, dict):
            manual_text = result.get("manual_text")
            if isinstance(manual_text, str):
                self.proxy_text.delete("1.0", "end")
                self.proxy_text.insert("1.0", manual_text)
            status = str(result.get("status", "预校验完成"))
            self.status_var.set(status)
            return
        self.status_var.set(str(result))

    def _save(self) -> None:
        self.on_save(self._collect_config())
        self.destroy()
