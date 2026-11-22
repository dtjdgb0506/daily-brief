# 把 site/ 部署到腾讯云 CloudBase 静态网站托管
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/deploy_cloudbase.ps1 `
#       -EnvId daily-brief-xxxx -SecretId xxx -SecretKey yyy
#
# 也可以在环境变量里给：
#   $env:TCB_ENV_ID / $env:TCB_SECRET_ID / $env:TCB_SECRET_KEY

param(
    [string]$EnvId = $env:TCB_ENV_ID,
    [string]$SecretId = $env:TCB_SECRET_ID,
    [string]$SecretKey = $env:TCB_SECRET_KEY,
    [string]$LocalPath = "site"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $EnvId -or -not $SecretId -or -not $SecretKey) {
    throw "缺参数：需要 -EnvId / -SecretId / -SecretKey（或环境变量 TCB_ENV_ID / TCB_SECRET_ID / TCB_SECRET_KEY）"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root $LocalPath))) {
    throw "找不到 $LocalPath，先跑 python scripts/build_card.py"
}

$node = "C:\Users\ROG\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
if (-not (Test-Path -LiteralPath $node)) { $node = "node" }
$pnpm = "C:\Users\ROG\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs"

Write-Host "→ 登录 CloudBase（API 密钥）"
& $node $pnpm --package=@cloudbase/cli dlx tcb login --apiKeyId $SecretId --apiKey $SecretKey

Write-Host "→ 部署 $LocalPath 到环境 $EnvId"
& $node $pnpm --package=@cloudbase/cli dlx tcb hosting deploy $LocalPath / -e $EnvId

Write-Host ""
Write-Host "完成。默认域名：https://$EnvId.tcloudbaseapp.com"
Write-Host "（第一次部署完，控制台 → 静态网站托管 里能看到访问域名）"
