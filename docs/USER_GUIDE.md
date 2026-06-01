# SakuraDownloader 使用文档

本文档覆盖桌面下载器、本地图库数据库、缩略图缓存、本地图库查询 API 的基本使用方式。

## 1. 环境准备

建议使用 Python 3.10 或更高版本。

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## 2. 启动桌面下载器

```bash
python main.py
```

当前基础平台：

- Pixiv
- X / Twitter
- 小红书
- 插件自动识别

## 3. 登录与 Cookie

推荐使用 Cookie 登录。可以通过浏览器插件导出 `cookies.txt` 或 json 文件，然后在 GUI 中导入。

Cookie、会话和浏览器 Profile 只保存在本机运行目录，不应提交到 GitHub。

## 4. 下载文件位置

默认下载目录：

```text
Sakura_Downloads/
```

打包运行时，本地数据目录：

```text
%LOCALAPPDATA%\SakuraDownloader
```

## 5. 本地图库

点击 GUI 中的“打开缩略图墙”可以启动本地图库服务，浏览缩略图、标签、收藏、作者页和本地导入记录。

## 6. 插件

内置插件随程序提供，用户也可以在“插件管理”中导入新的 Python 插件。

用户插件会保存到：

```text
%LOCALAPPDATA%\SakuraDownloader\plugins
```
