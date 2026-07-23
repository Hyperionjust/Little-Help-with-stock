# ═══════════════════════════════════════════════════════════════════
#  一键推送到 GitHub（PowerShell / Windows 版）
#
#  用法：在本文件夹右键 → "在终端中打开"（PowerShell），然后运行：
#        .\push_to_github.ps1
#
#  前提：电脑装了 Git（没有就去 https://git-scm.com/download/win 装）。
#  首次 push 时 Git 会弹浏览器让你登录 GitHub，照做即可（不用手填 token）。
# ═══════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"

# —— 你的仓库地址（已填好，可改）——
$Repo = "https://github.com/Hyperionjust/Little-Help-with-stock.git"

# —— 新版内容所在文件夹（本脚本所在目录）——
$Src  = $PSScriptRoot
$Tmp  = Join-Path $env:TEMP "Little-Help-with-stock"

# 1) 确认 git 身份（首次用 git 才需要）
if (-not (git config --global user.email)) {
    $email = Read-Host "你的 Git 邮箱"
    $name  = Read-Host "你的 Git 用户名"
    git config --global user.email $email
    git config --global user.name  $name
}

# 2) 克隆你现有的仓库到临时目录
if (Test-Path $Tmp) { Remove-Item $Tmp -Recurse -Force }
Write-Host "→ 克隆现有仓库..." -ForegroundColor Cyan
git clone $Repo $Tmp

# 3) 把新版内容拷进去（覆盖同名文件；跳过 .git / 缓存 / 运行产物）
Write-Host "→ 复制新版内容..." -ForegroundColor Cyan
robocopy $Src $Tmp /E /XD .git __pycache__ .pytest_cache outputs /XF *.pyc _pytest_out.txt | Out-Null

# 4) 提交并推送
Set-Location $Tmp
git add .
git commit -m "超进化：升级为 equity-research-suite 三层研报流水线（可对账研报生成）"
Write-Host "→ 推送到 GitHub（若弹登录窗口，用浏览器授权即可）..." -ForegroundColor Cyan
git push

Write-Host ""
Write-Host "✅ 完成！打开 https://github.com/Hyperionjust/Little-Help-with-stock 查看。" -ForegroundColor Green
