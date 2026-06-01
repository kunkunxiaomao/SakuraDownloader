$ErrorActionPreference = "Stop"

$Revision = "1217"
$BrowserVersion = "147.0.7727.15"
$InstallRoot = Join-Path $env:LOCALAPPDATA "ms-playwright"
$TempRoot = Join-Path $env:TEMP "pixivdownloader-playwright-install"

$Packages = @(
    @{
        Name = "Chromium"
        Target = "chromium-$Revision"
        Exe = "chrome-win64\chrome.exe"
        Url = "https://cdn.playwright.dev/builds/cft/$BrowserVersion/win64/chrome-win64.zip"
    },
    @{
        Name = "Chromium Headless Shell"
        Target = "chromium_headless_shell-$Revision"
        Exe = "chrome-headless-shell-win64\chrome-headless-shell.exe"
        Url = "https://cdn.playwright.dev/builds/cft/$BrowserVersion/win64/chrome-headless-shell-win64.zip"
    }
)

function Test-Installed($Package) {
    $exePath = Join-Path (Join-Path $InstallRoot $Package.Target) $Package.Exe
    return Test-Path $exePath
}

function Install-Package($Package) {
    $targetDir = Join-Path $InstallRoot $Package.Target
    $zipPath = Join-Path $TempRoot ($Package.Target + ".zip")
    $extractDir = Join-Path $TempRoot $Package.Target

    if (Test-Installed $Package) {
        Write-Host "[OK] $($Package.Name) already installed: $targetDir"
        return
    }

    Write-Host ""
    Write-Host "[DOWNLOAD] $($Package.Name)"
    Write-Host $Package.Url

    if (Test-Path $zipPath) {
        Remove-Item $zipPath -Force
    }
    if (Test-Path $extractDir) {
        Remove-Item $extractDir -Recurse -Force
    }

    Invoke-WebRequest -Uri $Package.Url -OutFile $zipPath -UseBasicParsing

    Write-Host "[EXTRACT] $($Package.Name)"
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

    if (Test-Path $targetDir) {
        Remove-Item $targetDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

    Get-ChildItem -Path $extractDir | ForEach-Object {
        Move-Item -Path $_.FullName -Destination $targetDir -Force
    }

    if (-not (Test-Installed $Package)) {
        throw "$($Package.Name) was not found after install."
    }

    Write-Host "[OK] $($Package.Name) installed: $targetDir"
}

Write-Host "SakuraDownloader - Playwright Chromium installer"
Write-Host "No Python required. Install location: $InstallRoot"
Write-Host ""

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

foreach ($package in $Packages) {
    Install-Package $package
}

Write-Host ""
Write-Host "Done. Reopen sakuradownloader.exe to use X / Xiaohongshu browser features."
Write-Host ""
Read-Host "Press Enter to exit"
