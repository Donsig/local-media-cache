#!/usr/bin/env pwsh
# Stage 7 Resilience Validation -- syncarr
# T0 baseline, T1 subscribe+deliver, T2 agent-kill mid-download,
# T3 aria2-kill mid-download, T4 server restart, T5 eviction
# Deferred: T6 (48h offline), T7 (7-day cruft) -- require elapsed real time
#
# Prerequisites: SSH aliases docker-host01 + satellite, PowerShell 7+

param(
    [string]$ClientId  = "",
    [string]$ProfileId = "passthrough"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$SERVER   = "http://192.168.1.176:8000"
$UI_TOKEN = "test"
$AG_TOKEN = "agent-syncarr-sat-D_30HEKtyMiqin7bWtmxGwESPpRk2QGM46MISx1G828"
$UH = @{ Authorization = "Bearer $UI_TOKEN"; "Content-Type" = "application/json" }
$AH = @{ Authorization = "Bearer $AG_TOKEN" }

$Results = [ordered]@{}
$SubId   = $null
$AssetId = $null
$MovieId = $null

function Step { param($msg) Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Pass  { param($name, $msg) $script:Results[$name] = "PASS"; Write-Host "  PASS [$name]: $msg" -ForegroundColor Green }
function Fail  { param($name, $msg) $script:Results[$name] = "FAIL: $msg"; Write-Host "  FAIL [$name]: $msg" -ForegroundColor Red; throw "[$name] $msg" }
function Info  { param($msg) Write-Host "  $msg" }
function Warn  { param($name, $msg) $script:Results[$name] = "WARN: $msg"; Write-Host "  WARN [$name]: $msg" -ForegroundColor Yellow }

function Get-Container {
    (ssh docker-host01 "docker ps --format '{{.Names}}'") | Where-Object { $_ -match "syncarr" } | Select-Object -First 1
}

function Get-Aria2Active {
    $payload = '{"jsonrpc":"2.0","id":"x","method":"aria2.tellActive"}'
    $raw = ssh satellite "curl -s --data '$payload' http://localhost:6800/jsonrpc"
    try {
        $result = ($raw | ConvertFrom-Json).result
        if ($result) { return @($result) }
    } catch { }
    return @()
}

function Wait-For {
    param([scriptblock]$Cond, [int]$TimeoutSec = 60, [int]$IntervalSec = 5, [string]$Label = "condition")
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        if (& $Cond) { return $true }
        Start-Sleep $IntervalSec
        $elapsed += $IntervalSec
        Info "  Waiting for $Label ... ($elapsed / $TimeoutSec s)"
    }
    return $false
}

# ============================================================
Step "T0: Baseline"
# ============================================================

try {
    $clientsResp = Invoke-RestMethod "$SERVER/api/clients" -Headers $UH
    Pass "T0.server" "Server API reachable"
} catch { Fail "T0.server" "Server unreachable: $_" }

$agentState = ssh satellite "systemctl --user is-active syncarr-agent"
if ($agentState -eq "active") { Pass "T0.agent" "syncarr-agent active" }
else { Fail "T0.agent" "syncarr-agent not active: $agentState" }

$aria2State = ssh satellite "systemctl --user is-active aria2"
if ($aria2State -eq "active") { Pass "T0.aria2" "aria2 active" }
else { Fail "T0.aria2" "aria2 not active: $aria2State" }

$container = Get-Container
Info "Container: $container"

if (-not $ClientId) { $ClientId = $clientsResp.clients[0].id }
Info "Client: $ClientId"

$snapCount = (ssh satellite "find ~/media -type f | wc -l").Trim()
Info "Existing files on satellite: $snapCount"

# ============================================================
Step "T1: Discover Oppenheimer + subscribe"
# ============================================================

$items = (Invoke-RestMethod "$SERVER/api/media/library/2/items?search=Taxi+4" -Headers $UH).items
$opp   = $items | Where-Object { $_.title -match "Taxi 4" } | Select-Object -First 1
if (-not $opp) { Fail "T1.find" "Taxi 4 not found in Plex library 2 (Film)" }
$MovieId = $opp.id
$sizeGb  = [math]::Round($opp.size_bytes / 1GB, 2)
Pass "T1.find" "Found id=$MovieId title=$($opp.title) size=${sizeGb}GB"

# Stop agent so we control when the download starts
ssh satellite "systemctl --user stop syncarr-agent"
Info "Agent stopped (controlling download timing)"
Start-Sleep 2

$subBody = @{ client_id = $ClientId; media_item_id = $MovieId; scope_type = "movie"; scope_params = $null; profile_id = $ProfileId } | ConvertTo-Json
$sub     = Invoke-RestMethod "$SERVER/api/subscriptions" -Method POST -Headers $UH -Body $subBody
$SubId   = $sub.id
Pass "T1.sub" "Subscription created id=$SubId"

# Passthrough -> stat+sha256 of source file; can take ~3 min for large files on NFS
$readyAssignment = $null
$ok = Wait-For -TimeoutSec 600 -IntervalSec 10 -Label "asset ready" -Cond {
    try {
        $view = Invoke-RestMethod "$SERVER/assignments" -Headers $AH -ErrorAction Stop
        $hit  = $view.assignments | Where-Object { $_.state -eq "ready" -and $_.source_media_id -eq $script:MovieId }
        if ($hit) { $script:readyAssignment = $hit[0]; return $true }
    } catch { }
    return $false
}
if (-not $ok) { Fail "T1.ready" "Asset never became ready within 5 min" }
$AssetId = $readyAssignment.asset_id
Pass "T1.ready" "Asset ready, asset_id=$AssetId"

# ============================================================
Step "T2: Agent kill mid-download"
# ============================================================

ssh satellite "systemctl --user start syncarr-agent"
Info "Agent started -- waiting for aria2 to pick up download..."

$ok = Wait-For -TimeoutSec 90 -IntervalSec 3 -Label "aria2 download start" -Cond {
    try {
        [array]$dl = Get-Aria2Active
        return ($dl.Count -gt 0 -and $dl[0] -ne $null -and [long]$dl[0].completedLength -gt 0)
    } catch { return $false }
}
if (-not $ok) { Fail "T2.start" "aria2 never started within 90s" }

[array]$active = Get-Aria2Active
$pct    = [math]::Round(100 * [long]$active[0].completedLength / [long]$active[0].totalLength, 2)
$dlMb   = [math]::Round([long]$active[0].completedLength / 1MB, 1)
Info "Download in flight: $pct% (${dlMb}MB) -- killing agent"

ssh satellite "systemctl --user stop syncarr-agent"
Pass "T2.kill" "Agent killed at $pct% / ${dlMb}MB"

Start-Sleep 3
if ((ssh satellite "systemctl --user is-active aria2") -eq "active") { Pass "T2.aria2survives" "aria2 kept running after agent kill" }
else { Fail "T2.aria2survives" "aria2 stopped when agent was killed" }

ssh satellite "systemctl --user start syncarr-agent"
Info "Agent restarted -- waiting for delivery (up to 10 min)..."

$ok = Wait-For -TimeoutSec 600 -IntervalSec 15 -Label "delivery confirmed" -Cond {
    try {
        $view = Invoke-RestMethod "$SERVER/assignments" -Headers $AH -ErrorAction Stop
        $pending = $view.assignments | Where-Object { $_.source_media_id -eq $script:MovieId -and $_.state -in @("ready","queued") }
        return $pending.Count -eq 0
    } catch { return $false }
}
if (-not $ok) { Fail "T2.deliver" "Delivery not confirmed within 10 min" }

$oppFile = ssh satellite "find ~/media -type f \( -name '*.mkv' -o -name '*.mp4' \) | grep -i "taxi.4" || true"
if ($oppFile) { Pass "T2.file" "File on satellite: $oppFile" }
else          { Fail "T2.file" "File NOT found on satellite after delivery" }

# ============================================================
Step "T3: aria2 kill mid-download (network interruption proxy)"
# ============================================================

# Reset: delete subscription (triggers eviction), wait for agent to clean up, re-subscribe
Info "Deleting subscription to trigger eviction before re-download test..."
Invoke-RestMethod "$SERVER/api/subscriptions/$SubId" -Method DELETE -Headers $UH | Out-Null

$ok = Wait-For -TimeoutSec 90 -IntervalSec 5 -Label "eviction before re-subscribe" -Cond {
    $f = ssh satellite "find ~/media -iname '*taxi*4*' -type f 2>/dev/null || true"
    [string]::IsNullOrWhiteSpace($f)
}
if (-not $ok) {
    # Force-delete if agent eviction is slow
    ssh satellite "find ~/media -iname '*taxi*4*' -type f -delete"
    Info "Force-deleted file (agent eviction was slow)"
}

# Re-subscribe to get a fresh assignment
$sub2 = Invoke-RestMethod "$SERVER/api/subscriptions" -Method POST -Headers $UH -Body $subBody
$SubId = $sub2.id
Info "Re-subscribed, new subscription id=$SubId"

# Wait for new asset to be ready
$readyAssignment = $null
$ok = Wait-For -TimeoutSec 300 -IntervalSec 5 -Label "asset ready (re-subscribed)" -Cond {
    try {
        $view = Invoke-RestMethod "$SERVER/assignments" -Headers $AH -ErrorAction Stop
        $hit  = $view.assignments | Where-Object { $_.state -eq "ready" -and $_.source_media_id -eq $script:MovieId }
        if ($hit) { $script:readyAssignment = $hit[0]; return $true }
    } catch { }
    return $false
}
if (-not $ok) { Fail "T3.ready" "Asset never became ready after re-subscribe within 10 min" }
$AssetId = $readyAssignment.asset_id
Pass "T3.ready" "Fresh asset ready, asset_id=$AssetId"
Start-Sleep 2

$ok = Wait-For -TimeoutSec 120 -IntervalSec 3 -Label "aria2 >5MB downloaded" -Cond {
    try {
        [array]$dl = Get-Aria2Active
        return ($dl.Count -gt 0 -and $dl[0] -ne $null -and [long]$dl[0].completedLength -gt (5 * 1024 * 1024))
    } catch { return $false }
}
if (-not $ok) { Fail "T3.start" ">5MB not downloaded within 120s" }

[array]$active = Get-Aria2Active
$mb = [math]::Round([long]$active[0].completedLength / 1MB, 1)
Info "Downloaded ${mb}MB -- killing aria2"

ssh satellite "systemctl --user stop aria2"
Pass "T3.kill" "aria2 killed at ${mb}MB"
Start-Sleep 2

ssh satellite "systemctl --user start aria2"
if ((ssh satellite "systemctl --user is-active aria2") -eq "active") { Pass "T3.restart" "aria2 restarted" }
else { Fail "T3.restart" "aria2 failed to restart" }

$ok = Wait-For -TimeoutSec 600 -IntervalSec 15 -Label "delivery after aria2 restart" -Cond {
    try {
        $view = Invoke-RestMethod "$SERVER/assignments" -Headers $AH -ErrorAction Stop
        $pending = $view.assignments | Where-Object { $_.source_media_id -eq $script:MovieId -and $_.state -in @("ready","queued") }
        return $pending.Count -eq 0
    } catch { return $false }
}
if (-not $ok) { Fail "T3.deliver" "Delivery not confirmed within 10 min after aria2 restart" }
Pass "T3.deliver" "Delivery confirmed -- aria2 session resume worked"

# ============================================================
Step "T4: Server container restart (state persistence)"
# ============================================================

$container = Get-Container
Info "Stopping container: $container (Swarm will restart)"
ssh docker-host01 "docker stop $container"

$ok = Wait-For -TimeoutSec 90 -IntervalSec 5 -Label "server recovery" -Cond {
    try { Invoke-RestMethod "$SERVER/api/clients" -Headers $UH -ErrorAction Stop | Out-Null; return $true } catch { return $false }
}
if (-not $ok) { Fail "T4.restart" "Server did not recover within 90s" }
$newContainer = Get-Container
Pass "T4.restart" "Server recovered; new container: $newContainer"

# Verify: asset still 'ready' in server (file was delivered, asset record persists)
$assets = (Invoke-RestMethod "$SERVER/api/assets?media_item_ids=$MovieId" -Headers $UH)
$taxiAsset = $assets | Where-Object { $_.asset_id -eq $AssetId }
Info "Post-restart asset: $($taxiAsset | ConvertTo-Json -Compress)"
if ($taxiAsset -and $taxiAsset.status -eq "ready") { Pass "T4.state" "Asset 'ready' state persisted through server restart" }
else { Fail "T4.state" "Asset state unexpected after restart: $($taxiAsset | ConvertTo-Json -Compress)" }

# ============================================================
Step "T5: Eviction"
# ============================================================

Invoke-RestMethod "$SERVER/api/subscriptions/$SubId" -Method DELETE -Headers $UH | Out-Null
Info "Subscription $SubId deleted"

$ok = Wait-For -TimeoutSec 120 -IntervalSec 5 -Label "file eviction" -Cond {
    $f = ssh satellite "find ~/media -iname '*taxi*4*' -type f 2>/dev/null || true"
    [string]::IsNullOrWhiteSpace($f)
}
if (-not $ok) { Fail "T5.file" "File not removed from satellite within 120s" }
Pass "T5.file" "Oppenheimer file evicted from satellite"

Start-Sleep 5
# Verify: agent view is clean (no pending/ready/evict for this movie)
$view = Invoke-RestMethod "$SERVER/assignments" -Headers $AH
$remaining = $view.assignments | Where-Object { $_.source_media_id -eq $MovieId }
Info "Post-eviction agent view for movie $MovieId`: $($remaining | ConvertTo-Json -Compress)"
if ($remaining.Count -eq 0) { Pass "T5.gc" "No pending assignments for movie (GC'd or confirmed evicted)" }
else { Warn "T5.gc" "Unexpected assignments still visible: $($remaining | ConvertTo-Json -Compress)" }

# ============================================================
Step "SUMMARY"
# ============================================================

Write-Host ""
Write-Host "Stage 7 Resilience Validation" -ForegroundColor Yellow
Write-Host "==============================" -ForegroundColor Yellow
foreach ($key in $Results.Keys) {
    $val   = $Results[$key]
    $color = switch -Wildcard ($val) { "PASS" { "Green" } "FAIL*" { "Red" } default { "Yellow" } }
    Write-Host ("  {0,-25} {1}" -f $key, $val) -ForegroundColor $color
}
Write-Host ""
Write-Host "Deferred (elapsed time required):" -ForegroundColor Gray
Write-Host "  T6: 48h offline            manual -- power off satellite, bring back after trip" -ForegroundColor Gray
Write-Host "  T7: 7-day cruft check      manual -- leave running, check orphan rows/files" -ForegroundColor Gray
Write-Host "  Transcode worker restart   not covered -- Oppenheimer uses passthrough" -ForegroundColor Gray
