# 把 site/ 发布出去（GitHub Pages）或本地预览
#
# 本地预览（默认）：
#   powershell -ExecutionPolicy Bypass -File scripts/deploy_pages.ps1 -Local
#   → http://127.0.0.1:8080  手机同一 WiFi 下可用 http://<电脑IP>:8080
#
# 发布到 GitHub Pages（需要先在 GitHub 建好空仓库）：
#   powershell -ExecutionPolicy Bypass -File scripts/deploy_pages.ps1 `
#       -RemoteUrl https://github.com/<你>/<仓库>.git `
#       -WebBase https://<你>.github.io/<仓库>
#   -WebBase 会顺手写进 config/profile.toml，之后卡片链接就是真地址。

param(
    [switch]$Local,
    [int]$Port = 8080,
    [string]$RemoteUrl = "",
    [string]$WebBase = "",
    [string]$Branch = "gh-pages"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Site = Join-Path $Root "site"

if (-not (Test-Path -LiteralPath $Site)) {
    throw "找不到 site/：先跑一次 python scripts/build_card.py"
}

# 找 python
$Python = "python.exe"
$candidate = "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if (Test-Path -LiteralPath $candidate) { $Python = $candidate }

if ($Local) {
    Write-Host "本地预览：http://127.0.0.1:$Port"
    Write-Host "（手机同 WiFi 可用 http://<电脑IP>:$Port）"
    & $Python -m http.server $Port --directory $Site
    exit 0
}

if (-not $RemoteUrl) {
    Write-Host "没给 -RemoteUrl，只打印步骤："
    Write-Host ""
    Write-Host "1. 在 GitHub 建一个空仓库（不要初始化 README）"
    Write-Host "2. 重跑本脚本："
    Write-Host "   powershell -ExecutionPolicy Bypass -File scripts/deploy_pages.ps1 -RemoteUrl https://github.com/<你>/<仓库>.git -WebBase https://<你>.github.io/<仓库>"
    Write-Host "3. 仓库 Settings → Pages → Source 选分支 $Branch / 根目录"
    exit 0
}

$tmp = Join-Path $env:TEMP ("dailybrief-pages-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
Copy-Item -Path (Join-Path $Site "*") -Destination $tmp -Recurse -Force
Set-Content -LiteralPath (Join-Path $tmp ".nojekyll") -Value "" -Encoding utf8

git -C $tmp init -q
git -C $tmp checkout -q -b $Branch
git -C $tmp add -A
git -C $tmp -c user.name="dailybrief" -c user.email="dailybrief@local" commit -q -m "deploy $(Get-Date -Format yyyy-MM-dd)"
git -C $tmp remote add origin $RemoteUrl
git -C $tmp push -f origin "$Branch"

Remove-Item -LiteralPath $tmp -Recurse -Force

if ($WebBase) {
    $profile = Join-Path $Root "config\profile.toml"
    $text = Get-Content -LiteralPath $profile -Raw -Encoding utf8
    $text = [regex]::Replace($text, 'web_base\s*=\s*"[^"]*"', 'web_base = "' + $WebBase + '"')
    Set-Content -LiteralPath $profile -Value $text -Encoding utf8 -NoNewline
    Write-Host "已把 web_base 改成 $WebBase"
}

Write-Host "已发布到分支 $Branch。"
Write-Host "等 1-2 分钟后访问：$WebBase"
Write-Host "（第一次要在 GitHub 仓库 Settings → Pages 里把 Source 设成 $Branch）"
