@echo off
setlocal
title SakuraDownloader - Install Playwright Chromium

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_playwright_chromium.ps1"

endlocal
