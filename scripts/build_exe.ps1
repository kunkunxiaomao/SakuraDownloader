$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$spec = Join-Path $root "SakuraDownloader.spec"

Set-Location $root

Write-Host "Building sakuradownloader.exe..."
Write-Host "Sensitive runtime data is not bundled: runtime/, profiles/, Sakura_Downloads/, *.db, session/cookie files"

python -m PyInstaller --clean --noconfirm $spec

Write-Host ""
Write-Host "Done: dist\sakuradownloader.exe"
Write-Host "Runtime cookies/session will be stored under %LOCALAPPDATA%\SakuraDownloader."
