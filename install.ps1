param(
    [string]$Target = "",
    [ValidateSet("claude_code", "openclaw")]
    [string]$Harness = "claude_code",
    [switch]$Link,
    [switch]$Uninstall
)

# FaultTree-SCoT skills installer (quick copy) — Windows PowerShell 版。
# 把本仓 skills\ 下的四个 skill 拷贝(或 -Link 软链)到 harness 的 skills 目录。
#   用法:
#     .\install.ps1                    # 拷贝到 ~\.claude\skills(默认)
#     .\install.ps1 -Link              # 软链(需开发者模式或管理员)
#     .\install.ps1 -Target C:\path    # 自定义目标
#     .\install.ps1 -Harness openclaw  # 目标默认 ~\.openclaw\skills
#     .\install.ps1 -Uninstall         # 移除已安装的拷贝/软链
#   依赖:仅 Python 标准库;读取 .xlsx 需 openpyxl(csv 开箱即用):pip install openpyxl

$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Message) Write-Host "[install] $Message" }

if (-not $Target) {
    if ($Harness -eq "openclaw") {
        $Target = Join-Path $env:USERPROFILE ".openclaw\skills"
    } else {
        $Target = Join-Path $env:USERPROFILE ".claude\skills"
    }
}

$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillsSrc = Join-Path $Source "skills"
$Mode = if ($Link) { "link" } else { "copy" }
$Dirs = @("cs-scot-generate", "cs-scot-execute", "fault-tree-rca")

Write-Step "source:  $SkillsSrc"
Write-Step "target:  $Target"
Write-Step "harness: $Harness"
Write-Step "mode:    $Mode"

if ($Uninstall) {
    foreach ($d in $Dirs) {
        $dst = Join-Path $Target $d
        if (Test-Path -LiteralPath $dst) {
            Write-Step "removing $dst"
            Remove-Item -LiteralPath $dst -Recurse -Force
        }
    }
    Write-Step "uninstall complete"
    exit 0
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null

foreach ($d in $Dirs) {
    $srcDir = Join-Path $SkillsSrc $d
    $dstDir = Join-Path $Target $d
    if (-not (Test-Path -LiteralPath $srcDir -PathType Container)) {
        Write-Error "[install] missing source skill: $srcDir"
        exit 2
    }
    if (Test-Path -LiteralPath $dstDir) {
        Write-Step "replacing $dstDir"
        Remove-Item -LiteralPath $dstDir -Recurse -Force
    }
    if ($Link) {
        try {
            New-Item -ItemType SymbolicLink -Path $dstDir -Target $srcDir | Out-Null
        } catch {
            Write-Error "[install] symlink failed. 开启 Windows 开发者模式或以管理员运行,或去掉 -Link 改为拷贝。Detail: $($_.Exception.Message)"
            exit 2
        }
        Write-Step "linked   $d"
    } else {
        Copy-Item -Path $srcDir -Destination $dstDir -Recurse -Force
        Write-Step "copied   $d"
    }
}

Write-Host ""
Write-Step "DONE. 已安装 3 个 skill 到:$Target"
Write-Host "  - cs-scot-generate / cs-scot-execute / fault-tree-rca"
Write-Host "依赖:仅 Python 标准库;读取 .xlsx 需 openpyxl(csv 开箱即用): pip install openpyxl"
