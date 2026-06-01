from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from pixiv_app.core.paths import plugin_roots, user_plugins_root
from pixiv_app.core.plugin.base import BasePlugin
from pixiv_app.core.plugin.generator import PluginGenerator
from pixiv_app.core.plugin.manager import PluginManager


def _slugify_plugin_name(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", value.strip())
    text = text.strip("._-")
    return text or "imported_plugin"


class PluginPanel(ctk.CTkFrame):
    """Generic plugin list, import, reload, and template tools."""

    def __init__(
        self,
        parent,
        plugin_manager: PluginManager,
        *,
        user_root: Path | None = None,
        text_color: str = "#1e3a5f",
        muted_color: str = "#6f8dac",
        border_color: str = "#d6e8fb",
        danger_color: str = "#eb7d7d",
    ) -> None:
        super().__init__(parent, fg_color="transparent")
        self.manager = plugin_manager
        self.user_root = user_root or user_plugins_root()
        self._text_color = text_color
        self._muted_color = muted_color
        self._border_color = border_color
        self._danger_color = danger_color

        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(fill="x", padx=12, pady=(10, 6))
        ctk.CTkLabel(
            title_frame,
            text="插件管理",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=text_color,
        ).pack(side="left")

        btn_row = ctk.CTkFrame(title_frame, fg_color="transparent")
        btn_row.pack(side="right")
        ctk.CTkButton(btn_row, text="导入 .py", width=88, command=self.import_python_plugin).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="生成模板", width=88, command=self.open_create_dialog).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="重新加载", width=88, command=self.reload_all).pack(side="left", padx=4)

        hint = (
            "导入一个 Python 插件文件即可扩展站点；插件需导出 plugin_class，"
            "并继承 pixiv_app.core.plugin.base.BasePlugin。"
        )
        ctk.CTkLabel(
            self,
            text=hint,
            text_color=muted_color,
            font=ctk.CTkFont(size=12),
            wraplength=760,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        path_row = ctk.CTkFrame(self, fg_color="transparent")
        path_row.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(path_row, text="用户插件目录：", text_color=text_color, font=ctk.CTkFont(size=12, weight="bold")).pack(
            side="left"
        )
        ctk.CTkLabel(path_row, text=str(self.user_root), text_color=muted_color, font=ctk.CTkFont(size=11)).pack(
            side="left", fill="x", expand=True
        )

        self.container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.error_container = ctk.CTkFrame(self, fg_color="transparent")
        self.error_container.pack(fill="x", padx=8, pady=(0, 8))

        self.log_text = ctk.CTkTextbox(self, height=120, font=ctk.CTkFont(family="Consolas", size=11))
        self.log_text.pack(fill="x", padx=12, pady=(0, 12))

        self.refresh()

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")

    def refresh(self) -> None:
        for child in self.container.winfo_children():
            child.destroy()
        for child in self.error_container.winfo_children():
            child.destroy()
        if not self.manager.plugins:
            ctk.CTkLabel(
                self.container,
                text="没有发现插件。请点击「导入 .py」或「生成模板」。",
                text_color=self._muted_color,
                justify="left",
            ).pack(anchor="w", pady=8)
        for name, plugin in sorted(self.manager.plugins.items(), key=lambda x: x[0].lower()):
            self._add_plugin_card(name, plugin)
        self._add_error_cards()

    def _add_error_cards(self) -> None:
        if not self.manager.load_errors:
            return
        title = ctk.CTkLabel(
            self.error_container,
            text="加载失败的插件",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=self._danger_color,
        )
        title.pack(anchor="w", padx=4, pady=(8, 4))
        for path, error in sorted(self.manager.load_errors.items(), key=lambda x: str(x[0]).lower()):
            card = ctk.CTkFrame(self.error_container, corner_radius=10, border_width=1, border_color=self._danger_color)
            card.pack(fill="x", pady=4)
            ctk.CTkLabel(
                card,
                text=str(path),
                font=ctk.CTkFont(size=11),
                text_color=self._text_color,
                wraplength=760,
                justify="left",
            ).pack(anchor="w", padx=10, pady=(8, 2))
            ctk.CTkLabel(
                card,
                text=error,
                font=ctk.CTkFont(size=11),
                text_color=self._danger_color,
                wraplength=760,
                justify="left",
            ).pack(anchor="w", padx=10, pady=(0, 8))

    def _add_plugin_card(self, name: str, plugin: BasePlugin) -> None:
        card = ctk.CTkFrame(self.container, corner_radius=12, border_width=1, border_color=self._border_color)
        card.pack(fill="x", pady=6)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(top, text=plugin.name, font=ctk.CTkFont(size=15, weight="bold"), text_color=self._text_color).pack(
            side="left"
        )
        ctk.CTkLabel(top, text=f"v{plugin.version}", font=ctk.CTkFont(size=12), text_color=self._muted_color).pack(
            side="left", padx=10
        )
        st = "就绪" if plugin.validate() else "需配置"
        ctk.CTkLabel(top, text=st, font=ctk.CTkFont(size=12), text_color=self._muted_color).pack(side="right")

        path = self.manager.get_plugin_path(name)
        source = "用户插件" if path and self.user_root in path.parents else "内置插件"
        ctk.CTkLabel(
            card,
            text=f"域名 {plugin.domain}    来源 {source}",
            font=ctk.CTkFont(size=12),
            text_color=self._muted_color,
        ).pack(anchor="w", padx=12, pady=(0, 4))
        if path:
            ctk.CTkLabel(
                card,
                text=str(path),
                font=ctk.CTkFont(size=11),
                text_color=self._muted_color,
                wraplength=760,
                justify="left",
            ).pack(anchor="w", padx=12, pady=(0, 8))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(actions, text="重载", width=72, command=lambda n=name: self.reload_one(n)).pack(side="left", padx=(0, 6))
        if path and self.user_root in path.parents:
            ctk.CTkButton(
                actions,
                text="删除文件",
                width=86,
                fg_color=self._danger_color,
                command=lambda n=name: self.delete_user_plugin(n),
            ).pack(side="left")
        else:
            ctk.CTkButton(actions, text="从内存卸载", width=92, command=lambda n=name: self.unload_one(n)).pack(side="left")

    def import_python_plugin(self) -> None:
        path = filedialog.askopenfilename(
            title="导入 Python 插件",
            filetypes=[("Python plugin", "*.py"), ("All files", "*.*")],
        )
        if not path:
            return
        src = Path(path)
        if not src.is_file():
            messagebox.showerror("导入失败", "文件不存在。")
            return

        plugin_dir = self.user_root / _slugify_plugin_name(src.stem)
        index = 2
        while plugin_dir.exists() and (plugin_dir / "plugin.py").resolve() != src.resolve():
            plugin_dir = self.user_root / f"{_slugify_plugin_name(src.stem)}_{index}"
            index += 1
        plugin_dir.mkdir(parents=True, exist_ok=True)
        dst = plugin_dir / "plugin.py"

        try:
            shutil.copy2(src, dst)
            plugin = self.manager.load_plugin(dst)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        if plugin is None:
            messagebox.showerror("导入失败", "插件未能加载。请确认文件中导出了 plugin_class。")
            return

        self.refresh()
        self.log(f"已导入插件: {plugin.name} -> {dst}")
        messagebox.showinfo("导入完成", f"已导入插件：{plugin.name}\n\n{dst}")

    def reload_all(self) -> None:
        self.manager.reload_all_from_disk()
        self.refresh()
        self.log("已重新加载全部插件。")

    def reload_one(self, name: str) -> None:
        if self.manager.reload_plugin(name):
            self.refresh()
            self.log(f"已重载插件: {name}")
        else:
            self.log(f"重载失败: {name}")

    def unload_one(self, name: str) -> None:
        self.manager.unload_plugin(name)
        self.refresh()
        self.log(f"已从内存卸载: {name}")

    def delete_user_plugin(self, name: str) -> None:
        path = self.manager.get_plugin_path(name)
        if path is None or self.user_root not in path.parents:
            messagebox.showinfo("提示", "只能删除用户导入的插件文件。")
            return
        if not messagebox.askyesno("确认删除", f"将删除插件目录：\n{path.parent}\n\n是否继续？"):
            return
        self.manager.unload_plugin(name)
        shutil.rmtree(path.parent, ignore_errors=True)
        self.manager.reload_all_from_disk()
        self.refresh()
        self.log(f"已删除用户插件: {name}")

    def open_create_dialog(self) -> None:
        CreatePluginDialog(self, self.user_root, on_created=lambda: (self.reload_all(), self.refresh()))


class CreatePluginDialog(ctk.CTkToplevel):
    def __init__(self, parent, plugins_root: Path, *, on_created: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.title("生成插件模板")
        self.geometry("520x420")
        self.plugins_root = plugins_root
        self._on_created = on_created
        self.generator = PluginGenerator()

        pad = {"padx": 16, "pady": 8}
        ctk.CTkLabel(self, text="名称").pack(anchor="w", **pad)
        self.name_entry = ctk.CTkEntry(self, placeholder_text="例如 MySite")
        self.name_entry.pack(fill="x", **pad)

        ctk.CTkLabel(self, text="域名").pack(anchor="w", **pad)
        self.domain_entry = ctk.CTkEntry(self, placeholder_text="例如 example.com")
        self.domain_entry.pack(fill="x", **pad)

        ctk.CTkLabel(self, text="作者").pack(anchor="w", **pad)
        self.author_entry = ctk.CTkEntry(self)
        self.author_entry.pack(fill="x", **pad)

        ctk.CTkLabel(self, text="描述").pack(anchor="w", **pad)
        self.desc_text = ctk.CTkTextbox(self, height=100)
        self.desc_text.pack(fill="x", **pad)

        ctk.CTkButton(self, text="生成到用户插件目录", command=self._generate).pack(pady=16)

    def _generate(self) -> None:
        data = {
            "name": self.name_entry.get().strip(),
            "domain": self.domain_entry.get().strip(),
            "author": self.author_entry.get().strip(),
            "description": self.desc_text.get("1.0", "end").strip(),
        }
        if not data["name"] or not data["domain"]:
            messagebox.showerror("校验失败", "请填写名称与域名。")
            return
        try:
            path = self.generator.generate_from_gui(data, output_root=self.plugins_root)
        except Exception as exc:
            messagebox.showerror("失败", str(exc))
            return
        messagebox.showinfo("完成", f"已生成: {path}")
        self.destroy()
        if self._on_created:
            self._on_created()


class PluginPanelWindow(ctk.CTkToplevel):
    """Standalone window hosting PluginPanel."""

    def __init__(self, parent, *, cookie: str = "") -> None:
        super().__init__(parent)
        self.title("插件管理")
        self.geometry("760x620")
        user_root = user_plugins_root()
        self.manager = PluginManager(plugin_roots())
        self.manager.load_all()
        pixiv = self.manager.plugins.get("Pixiv")
        if pixiv is not None and hasattr(pixiv, "set_auth"):
            pixiv.set_auth(cookie)
        panel = PluginPanel(self, self.manager, user_root=user_root)
        panel.pack(fill="both", expand=True)
