# start_dev.ps1 — Start local dev environment with auto-updating webhook
# Usage: Right-click → "Run with PowerShell"  OR  powershell -ExecutionPolicy Bypass -File start_dev.ps1

$CLOUDFLARED  = "C:\Users\livya\ngrok_bin\cloudflared.exe"
$ENV_FILE     = "$PSScriptRoot\.env"

Write-Host "`n=== Shan-AI local dev startup ===" -ForegroundColor Cyan

# ── 1. Kill any existing cloudflared ──────────────────────────────────────────
Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 1

# ── 2. Start cloudflared and capture URL from its output ──────────────────────
Write-Host "Starting Cloudflare tunnel..." -ForegroundColor Yellow
$cfLog = "$env:TEMP\cf_tunnel.log"
Remove-Item $cfLog -ErrorAction SilentlyContinue

$cfProcess = Start-Process -FilePath $CLOUDFLARED `
    -ArgumentList "tunnel --url http://localhost:8000" `
    -RedirectStandardError $cfLog `
    -NoNewWindow -PassThru

# Wait up to 20 s for the URL to appear in the log
$tunnelUrl = $null
$deadline  = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    Start-Sleep 1
    if (Test-Path $cfLog) {
        $line = Select-String -Path $cfLog -Pattern "https://[a-z0-9\-]+\.trycloudflare\.com" |
                Select-Object -First 1
        if ($line) {
            $tunnelUrl = ($line.Line -replace '.*?(https://[a-z0-9\-]+\.trycloudflare\.com).*', '$1').Trim()
            break
        }
    }
}

if (-not $tunnelUrl) {
    Write-Host "ERROR: Could not get Cloudflare tunnel URL. Check $cfLog" -ForegroundColor Red
    exit 1
}

$webhookUrl = "$tunnelUrl/telegram/webhook"
Write-Host "Tunnel URL: $webhookUrl" -ForegroundColor Green

# ── 3. Update .env with new webhook URL ───────────────────────────────────────
(Get-Content $ENV_FILE) -replace 'TELEGRAM_WEBHOOK_URL=.*', "TELEGRAM_WEBHOOK_URL=$webhookUrl" |
    Set-Content $ENV_FILE
Write-Host ".env updated." -ForegroundColor Gray

# ── 4. Wait for tunnel to be reachable (DNS propagation) ──────────────────────
Write-Host "Waiting for tunnel to be reachable..." -ForegroundColor Yellow
$ready    = $false
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    Start-Sleep 2
    try {
        $r = Invoke-WebRequest -Uri $tunnelUrl -Method GET -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        # Any response (even 404/405) means the tunnel is up
        $ready = $true
        break
    } catch {
        # 404/405/422 from FastAPI means tunnel + app are reachable
        if ($_.Exception.Response -ne $null) {
            $ready = $true
            break
        }
    }
}

if (-not $ready) {
    Write-Host "WARNING: Tunnel may not be reachable yet. Proceeding anyway..." -ForegroundColor DarkYellow
}

# ── 5. Restart FastAPI container to load new .env and re-register webhook ─────
Write-Host "Restarting FastAPI container..." -ForegroundColor Yellow
docker-compose up -d --force-recreate fastapi 2>&1 | Select-String -Pattern "(Started|Running|healthy|Recreated|Error)" | ForEach-Object { Write-Host $_.Line }

Write-Host "`nDone! Bot is live at: $webhookUrl" -ForegroundColor Green
Write-Host "Cloudflare tunnel is running (this window must stay open)."
Write-Host "Press Ctrl+C to stop.`n"

# Keep script open so cloudflared stays running
Wait-Process -Id $cfProcess.Id
