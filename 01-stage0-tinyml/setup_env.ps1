# 阶段 0 环境一键搭建脚本（Windows PowerShell）
#
# 用法（PowerShell，允许临时脚本执行）：
#     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#     .\setup_env.ps1
#
# 脚本会做五件事：
#   1. 创建 venv（Python 3.13）
#   2. 升级 pip
#   3. 用清华镜像安装训练依赖（requirements.txt）
#   4. 用清华镜像安装导出依赖（requirements-export.txt，tensorflow-cpu）
#   5. 跑一次环境自检
#
# 全程约 10-30 分钟（取决于网速），主要耗时在 torch 的 122 MB 轮子
# 和 tensorflow-cpu 的 351 MB 轮子。
#
# 为什么 3 和 4 要分两步装（中间还插一次验证）：
#   一次性装大依赖时，pip 的依赖解析可能耗时极长，甚至触发对已装包的
#   大规模卸载重装而卡死。分批装 + 中间验证，出问题能立刻定位是哪一批。
#   这是真实踩过的坑，详见 手册-阶段0.md 第 7 章。

$ErrorActionPreference = "Stop"

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "  阶段 0 · TinyML 环境搭建" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

$Root  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv  = Join-Path $Root "venv"
$PyExe = Join-Path $Venv "Scripts\python.exe"
$Mirror = "https://pypi.tuna.tsinghua.edu.cn/simple"

# ---- 1. 选 Python 解释器 ----
Write-Host "[1/4] 选择 Python 解释器..." -ForegroundColor Yellow

$ManagedPy = "C:\Users\$env:USERNAME\.workbuddy\binaries\python\versions\3.13.12\python.exe"
$Python = $null

if (Test-Path $ManagedPy) {
    $Python = $ManagedPy
    Write-Host "      使用托管 Python 3.13.12：$Python"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $ver = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ([version]$ver -lt [version]"3.10") {
        Write-Host "      [错误] 系统 Python 版本 $ver 过低，需要 >= 3.10" -ForegroundColor Red
        exit 1
    }
    $Python = "python"
    Write-Host "      使用系统 Python $ver"
} else {
    Write-Host "      [错误] 找不到 Python。请先安装 Python 3.10+ 并勾选 Add to PATH" -ForegroundColor Red
    exit 1
}

# ---- 2. 创建 venv ----
if (Test-Path $PyExe) {
    Write-Host "[2/4] venv 已存在，跳过创建" -ForegroundColor Yellow
} else {
    Write-Host "[2/4] 创建虚拟环境..." -ForegroundColor Yellow
    & $Python -m venv $Venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "      [错误] venv 创建失败" -ForegroundColor Red
        exit 1
    }
    Write-Host "      venv 就绪：$Venv"
}

# ---- 3. 装训练依赖 ----
Write-Host "[3/5] 安装训练依赖（清华镜像，约 5-15 分钟）..." -ForegroundColor Yellow
& $PyExe -m pip install --upgrade pip -i $Mirror
if ($LASTEXITCODE -ne 0) { Write-Host "      [警告] pip 升级失败，继续尝试" -ForegroundColor DarkYellow }

function Install-Req {
    param([string]$ReqFile)

    $Req = Join-Path $Root $ReqFile
    & $PyExe -m pip install -r $Req -i $Mirror
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "      [提示] 镜像源安装失败，改用官方源重试..." -ForegroundColor DarkYellow
        & $PyExe -m pip install -r $Req
        if ($LASTEXITCODE -ne 0) {
            Write-Host "      [错误] 依赖安装失败，请检查网络或代理设置" -ForegroundColor Red
            exit 1
        }
    }
}

Install-Req "requirements.txt"
Write-Host "      训练依赖安装完成" -ForegroundColor Green

# 中间验证：确认 torch 真的能 import。
# 不要在没验证的情况下继续装下一批——一旦后面的安装破坏了 torch，
# 有这一步就能立刻知道是第二批的锅。
Write-Host "      验证 torch 可用性..." -ForegroundColor Gray
& $PyExe -c "import torch; print('      torch', torch.__version__)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "      [错误] torch 安装后无法导入，请修复后再继续" -ForegroundColor Red
    exit 1
}

# ---- 4. 装导出依赖 ----
Write-Host "[4/5] 安装导出依赖 tensorflow-cpu（约 5-15 分钟）..." -ForegroundColor Yellow
Install-Req "requirements-export.txt"
Write-Host "      导出依赖安装完成" -ForegroundColor Green

# ---- 5. 自检 ----
Write-Host "[5/5] 运行环境自检..." -ForegroundColor Yellow
& $PyExe (Join-Path $Root "00-check-env.py")

Write-Host ""
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "  环境搭建完成" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "下一步："
Write-Host "  1. 激活环境：  .\venv\Scripts\Activate.ps1"
Write-Host "  2. 冒烟测试：  python src\train.py --smoke"
Write-Host "  3. 正式训练：  python src\train.py"
Write-Host ""
