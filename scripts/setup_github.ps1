# 一次性把仓库建好、密钥配好、Pages 打开。
#
# 方式 A（推荐）：先登录 gh，再跑本脚本
#   gh auth login
#   powershell -ExecutionPolicy Bypass -File scripts/setup_github.ps1
#
# 方式 B：给我一个 PAT（classic: repo + workflow + pages 权限），用环境变量传
#   $env:GH_TOKEN = "ghp_xxx"
#   powershell -ExecutionPolicy Bypass -File scripts/setup_github.ps1 -RepoName daily-brief
#
# 脚本会：建仓库 → 推代码 → 把 .env 里的 MAIL_*/LLM_* 写成 Actions Secrets →
#         打开 Pages（Source = GitHub Actions）→ 把 web_base 改成本仓库的 Pages 地址并再推一次。

param(
    [string]$RepoName = "daily-brief",
    [switch]$Private,
    [string]$Owner = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Get-EnvMap([string]$Path) {
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $map }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#") -or -not $t.Contains("=")) { continue }
        $k, $v = $t.Split("=", 2)
        $map[$k.Trim()] = $v.Trim()
    }
    return $map
}

$envMap = Get-EnvMap (Join-Path $Root ".env")
if (-not $envMap.ContainsKey("MAIL_USER")) { throw ".env 里没有 MAIL_USER" }

gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "gh 没登录：先跑 gh auth login（或设置 `$env:GH_TOKEN）" }

if (-not $Owner) { $Owner = gh api user --jq .login }
$visibility = if ($Private) { "--private" } else { "--public" }

Write-Host "→ 创建仓库 $Owner/$RepoName ($visibility)"
gh repo create "$Owner/$RepoName" $visibility --source . --remote origin --push 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  仓库可能已存在，直接 push"
    git remote remove origin 2>$null
    git remote add origin "https://github.com/$Owner/$RepoName.git"
    git push -u origin HEAD
}

Write-Host "→ 写入 Actions Secrets"
$secretKeys = @(
    "MAIL_SMTP_HOST", "MAIL_SMTP_PORT", "MAIL_USER", "MAIL_PASS",
    "MAIL_FROM", "MAIL_FROM_NAME", "MAIL_TO",
    "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"
)
foreach ($k in $secretKeys) {
    if ($envMap.ContainsKey($k) -and $envMap[$k]) {
        gh secret set $k --repo "$Owner/$RepoName" --body $envMap[$k] | Out-Null
        Write-Host "  set $k"
    }
}

Write-Host "→ 打开 Pages（Source = GitHub Actions）"
gh api -X POST "repos/$Owner/$RepoName/pages" -f "build_type=workflow" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    gh api -X PUT "repos/$Owner/$RepoName/pages" -f "build_type=workflow" 2>$null | Out-Null
}

$webBase = "https://$Owner.github.io/$RepoName"
Write-Host "→ 把 web_base 改成 $webBase"
$profile = Join-Path $Root "config\profile.toml"
$text = Get-Content -LiteralPath $profile -Raw -Encoding utf8
$text = [regex]::Replace($text, 'web_base\s*=\s*"[^"]*"', 'web_base = "' + $webBase + '"')
Set-Content -LiteralPath $profile -Value $text -Encoding utf8 -NoNewline

git add config/profile.toml
git -c user.name="dailybrief" -c user.email="dailybrief@local" commit -m "配置 Pages 地址" | Out-Null
git push | Out-Null

Write-Host ""
Write-Host "完成："
Write-Host "  仓库     https://github.com/$Owner/$RepoName"
Write-Host "  网页版   $webBase"
Write-Host "  定时     每天 07:30（GitHub Actions，电脑关机也跑）"
Write-Host "  首次先到 Actions 页手动点一次 Run workflow，确认能发信。"
