from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _class_name_from_plugin_name(name: str) -> str:
    parts = re.split(r"[\s_]+", name.strip())
    stem = "".join(p[:1].upper() + p[1:].lower() if p else "" for p in parts if p)
    return f"{stem}Plugin" if stem else "GeneratedPlugin"


class PluginGenerator:
    """Generate a skeleton plugin directory from Jinja2 templates."""

    def __init__(self, template_dir: str | Path | None = None) -> None:
        base = Path(__file__).resolve().parent / "templates"
        self.template_dir = Path(template_dir) if template_dir else base
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(enabled_extensions=()),
        )

    def generate(
        self,
        name: str,
        domain: str,
        author: str,
        description: str,
        *,
        output_root: str | Path = "plugins",
    ) -> Path:
        slug = name.strip().lower().replace(" ", "_")
        plugin_dir = Path(output_root) / slug
        plugin_dir.mkdir(parents=True, exist_ok=True)

        context: dict[str, Any] = {
            "name": name.strip(),
            "domain": domain.strip(),
            "author": author.strip(),
            "description": description.strip(),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "class_name": _class_name_from_plugin_name(name),
        }

        py_template = self.env.get_template("base_plugin.py.jinja")
        (plugin_dir / "plugin.py").write_text(py_template.render(**context), encoding="utf-8")

        manifest_template = self.env.get_template("manifest.json.jinja")
        (plugin_dir / "manifest.json").write_text(manifest_template.render(**context), encoding="utf-8")

        init_file = plugin_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text('"""Generated plugin."""\n', encoding="utf-8")

        return plugin_dir.resolve()

    def generate_from_gui(self, form_data: dict[str, str], *, output_root: str | Path = "plugins") -> Path:
        return self.generate(
            name=form_data["name"],
            domain=form_data["domain"],
            author=form_data["author"],
            description=form_data["description"],
            output_root=output_root,
        )
