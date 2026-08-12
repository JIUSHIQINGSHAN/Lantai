<#
.SYNOPSIS
目识·截屏入忆（v0.12）：把剪贴板截图或本地图片转 data URI，写入兰台记忆（POST /add）。

.DESCRIPTION
用法：
  1) 按 Win+Shift+S 框选截图（自动进剪贴板），然后运行：
     .\scripts\screenshot_memory.ps1 -Title "需求截图"
  2) 或直接指定图片文件：
     .\scripts\screenshot_memory.ps1 -FromFile C:\tmp\shot.png -Lane fact
  3) 只预演不写库（打印 data URI 大小）：
     .\scripts\screenshot_memory.ps1 -DryRun

剪贴板读取需要 STA 线程：pwsh 7 默认 MTA 时脚本会自动用 powershell.exe（5.1，
默认 STA）重入；也可直接改用 -FromFile 指定图片。

.PARAMETER FromFile 本地图片路径（缺省读剪贴板图片）。
.PARAMETER Title    记忆标题（缺省「截屏 yyyy-MM-dd HH:mm」）。
.PARAMETER Lane     分轨：fact/rule/experience/preference/chat/general（默认 general）。
.PARAMETER BaseUri  兰台地址（默认 http://127.0.0.1:8767）。
.PARAMETER ApiKey   API Key（默认读 $env:LANTAI_API_KEY；空 = 无鉴权回环模式）。
.PARAMETER DryRun   只构造 data URI 不调用 /add。
#>
param(
    [string]$FromFile = "",
    [string]$Title = "",
    [string]$Lane = "general",
    [string]$BaseUri = "http://127.0.0.1:8767",
    [string]$ApiKey = $env:LANTAI_API_KEY,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"

# 剪贴板读取需要 STA：pwsh 7 默认 MTA → 用 powershell.exe（默认 STA）重入
if (-not $FromFile -and [System.Threading.Thread]::CurrentThread.ApartmentState -ne [System.Threading.ApartmentState]::STA) {
    $reArgs = @()
    foreach ($key in $PSBoundParameters.Keys) {
        $val = $PSBoundParameters[$key]
        if ($val -is [switch]) {
            if ($val) { $reArgs += "-$key" }
        }
        elseif ($null -ne $val -and "$val" -ne "") {
            $reArgs += "-$key"; $reArgs += "$val"
        }
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File $PSCommandPath @reArgs
    exit $LASTEXITCODE
}

# ── 1) 取图：文件优先，否则剪贴板 ──
$bytes = $null
if ($FromFile) {
    if (-not (Test-Path -LiteralPath $FromFile)) {
        throw "图片文件不存在：$FromFile"
    }
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $FromFile))
}
else {
    try { Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop } catch { }
    try { Add-Type -AssemblyName System.Drawing -ErrorAction Stop } catch { }
    $img = [System.Windows.Forms.Clipboard]::GetImage()
    if (-not $img) {
        throw "剪贴板没有图片：请先按 Win+Shift+S 框选截图，或用 -FromFile 指定图片文件"
    }
    $tmp = Join-Path $env:TEMP ("lantai-shot-" + [guid]::NewGuid().ToString("N") + ".png")
    try {
        $img.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)
        $bytes = [System.IO.File]::ReadAllBytes($tmp)
    }
    finally {
        $img.Dispose()
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
    }
}

# ── 2) 转 data URI ──
$b64 = [Convert]::ToBase64String($bytes)
$dataUri = "data:image/png;base64,$b64"
$sizeMb = [math]::Round($bytes.Length / 1MB, 2)
Write-Host "已编码图片：$($bytes.Length) 字节（${sizeMb} MB）→ data URI $($dataUri.Length) 字符"

if ($DryRun) {
    Write-Host "DryRun：未写入（配好 LLM 密钥后去掉 -DryRun 即可入忆）。"
    return
}

# ── 3) POST /add ──
if (-not $Title) { $Title = "截屏 " + (Get-Date -Format "yyyy-MM-dd HH:mm") }
$body = @{ title = $Title; lane = $Lane; media_url = $dataUri } | ConvertTo-Json -Compress
$params = @{
    Uri         = "$BaseUri/add"
    Method      = "Post"
    Body        = $body
    ContentType = "application/json"
    TimeoutSec  = 180
}
if ($ApiKey) { $params["Headers"] = @{ "X-API-Key" = $ApiKey } }

$resp = Invoke-RestMethod @params
Write-Host "已写入记忆：document_id=$($resp.document_id) candidate_id=$($resp.candidate_id)"
Write-Host "标题：$Title｜lane=$Lane"
Write-Host "查看：/ui/vault 控制台，或 GET $BaseUri/documents/$($resp.document_id)"
