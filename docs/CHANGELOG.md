# 更新日志

## [未发布] — 任务队列与 SQLite 状态（渐进式改造）

### 新增

- **`pixiv_app/tasks/`**：统一任务模型（`DownloadTaskSpec`）、SQLite 指纹去重队列（`TaskQueue`）、批量输入解析（`parse_batch_for_mode`）、作者 **`artist_sync_watermark`** 水印读写（增量同步）。
- **`pixiv_app/runtime/`**：任务入队编排（`collect_task_specs`、`enqueue_specs_to_library`）、执行器（`TaskExecutor`：限速、错误分类、重试）、消费循环（`run_queue_until_idle`）。
- **数据库**（`pixiv_app/core/library.py`）：
  - 表 **`download_tasks`**：`pending` / `running` / `done` / `failed`、优先级、`fingerprint` 唯一约束、租约字段。
  - 表 **`artist_sync_watermark`**：每位作者 `last_synced_illust_id`。
  - **`artwork_files.content_hash`**：可选校验占位（默认空字符串，后续可由下载流程填入）。

### GUI（兼容旧路径）

- 性能设置中增加：
  - **「统一任务队列（SQLite 状态）」**（默认开启）：下载走 DB 队列 + worker；关闭后沿用原先内存直连逻辑。
  - **「作者增量同步（水印）」**：作者模式下仅排队大于当前水印的插画 ID（配合 Pixiv 递增 ID 假设）。
- 队列模式下校验支持 **多行 / 逗号 / 空格**、**`#标签`**（作者模式下按标签搜索再入队）。

### 说明 / 后续可做

- 分布式 worker 仍可后续改为远程 lease；当前单机多线程共享同一 SQLite 文件（WAL）与队列锁。
- `downloader` 仍负责具体 HTTP 与写库；队列与执行策略在 `runtime` 层，便于继续剥离重试策略或接入全局限速器。

---

## [未发布] — 插件化媒体平台（核心）

### 新增

- **`pixiv_app/core/plugin/base.py`**：`BasePlugin`、`Resource`、`PluginManifest`、异常类型；**同步** `parse` / `download` 与现有 requests 线程模型一致。
- **`pixiv_app/core/plugin/manager.py`**：`PluginManager`（`discover` / `load_plugin` / `unload_plugin` / `reload_all_from_disk`）、简单事件钩子。
- **`pixiv_app/core/plugin/generator.py`** + **`templates/`**：Jinja2 生成插件骨架（`plugin.py` + `manifest.json`）。
- **`pixiv_app/core/plugin/market.py`**：`PluginMarketClient` 占位（列表/安装可后续接 HTTP）。
- **`pixiv_app/core/orchestrator.py`**：`PluginTaskOrchestrator`（URL → 插件 → `Resource` 列表 / 下载委派）。
- **`plugins/pixiv/`**：内置 **Pixiv** 插件（封装 `downloader`/`download_binary`/`download_novel`，导出 **`plugin_class`**）。
- **`pixiv_app/gui/plugin_panel.py`**：插件列表、重载、卸载、模板生成；主窗口标题栏 **「插件管理」** 入口。
- **依赖**：`Jinja2>=3.1.0`（`requirements.txt`）。
- **测试**：`tests/test_plugin_manager.py`（`unittest` 发现与加载 Pixiv 插件）。

### 说明

- 主下载流程（任务队列 / 原 GUI 批量下载）**保持不变**；插件系统用于扩展站点与工具链，后续可把 `Resource` 映射到 `DownloadTaskSpec` 做深度合并。

---

## [未发布] — X (Twitter) Playwright 插件

### 新增

- **`plugins/x/`**：`plugin.py`（`BasePlugin`）、`playwright_client.py`、`x_urls.py`、`x_login.py`（持久化 Chromium 登录）、`manifest.json`、`setup_plugin.py`。
- **插件管理窗口**：**「X 登录」**（图形流程：`ready_signal` + `threading.Event`）、**「解析预览」** 输入框。
- **依赖**：`playwright`、`httpx`（见 `requirements.txt`）；首次需执行 `python -m playwright install chromium` 或 `python plugins/x/setup_plugin.py`。

### 说明

- 会话目录：`runtime/x_profile`；可选 `runtime/x_cookies.json`（Playwright cookie JSON 列表）。
- X 页面结构经常变更，解析选择器可能需按实际 DOM 微调。
