<#
    Prototype log parser for CScalp (FSR Launcher build).
    Throwaway validator: reads today's HTML logs, extracts trading events,
    prints them chronologically. Regex patterns are meant to port 1:1 to the
    Python service later.

    Usage:
      .\Parse-CScalpLogs.ps1                 # today's folder
      .\Parse-CScalpLogs.ps1 -Date 28.09.2026
      .\Parse-CScalpLogs.ps1 -LogRoot "C:\...\SubApps\CS\Log"
#>
param(
    [string]$LogRoot = "C:\Program Files (x86)\FSR Launcher\SubApps\CS\Log",
    [string]$Date = (Get-Date).ToString("dd.MM.yyyy")
)

$ErrorActionPreference = "Stop"

$dayDir = Join-Path $LogRoot $Date
if (-not (Test-Path $dayDir)) {
    Write-Host "No log folder for $Date at $dayDir" -ForegroundColor Yellow
    Write-Host "Available folders:" -ForegroundColor Yellow
    Get-ChildItem -Path $LogRoot -Directory | ForEach-Object { Write-Host "  $($_.Name)" }
    exit 1
}

# ru-RU decimal comma -> dot, then parse invariant
function ConvertTo-Num([string]$s) {
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    return [double]::Parse($s.Replace(" ", "").Replace(",", "."), [System.Globalization.CultureInfo]::InvariantCulture)
}

# Strip the HTML wrapper of a single log line, return "HH:MM:SS.mmm`tmessage"
function Get-LogLines([string]$path) {
    if (-not (Test-Path $path)) { return @() }
    $raw = Get-Content -Path $path -Raw -Encoding UTF8
    $out = New-Object System.Collections.Generic.List[object]
    # each entry: <div class="flexy"><div>[ts]</div>...<div...>message</div></div>
    $rx = [regex]'<div class="flexy">\s*<div>\[(?<ts>\d{2}:\d{2}:\d{2}\.\d{3})\]</div>.*?<div[^>]*>(?<msg>.*?)</div>\s*</div>'
    foreach ($m in $rx.Matches($raw)) {
        $msg = $m.Groups['msg'].Value -replace '<[^>]+>', ''   # drop inner <code> etc.
        $msg = [System.Net.WebUtility]::HtmlDecode($msg).Trim()
        $out.Add([PSCustomObject]@{ Ts = $m.Groups['ts'].Value; Msg = $msg })
    }
    return $out
}

$events = New-Object System.Collections.Generic.List[object]
function Add-Event($ts, $kind, $ticker, $side, $price, $amount, $orderId, $tradeId, $extra) {
    $events.Add([PSCustomObject]@{
        Ts = $ts; Kind = $kind; Ticker = $ticker; Side = $side
        Price = $price; Amount = $amount; OrderId = $orderId; TradeId = $tradeId; Extra = $extra
    })
}

# --------- Dispatcher_XDSD: fills, positions, order lifecycle ----------
$disp = Join-Path $dayDir "Dispatcher_XDSD.html"
$rxOnTrade = [regex]'Slot \((?<slot>\w+)\): Layer \((?<ticker>[^)]+)\): PM: OnTrade: ID=(?<tid>-?\d+); OrderID=(?<oid>-?\d+); Price=(?<price>[\d ,\.]+); Amount=(?<amt>[\d ,\.]+); Direction=(?<dir>\w+); Time=(?<time>[\d\. :]+)'
$rxPos = [regex]'Slot \((?<slot>\w+)\): Layer \((?<ticker>[^)]+)\): PM: OnPositionUpdate: Price=(?<price>[\d ,\.-]+); Amount=(?<amt>[\d ,\.-]+)'
$rxActive = [regex]'Slot \((?<slot>\w+)\): OrdersStorage: Add: OrderID=(?<oid>\d+) (?<state>add to active|remove from active)'

foreach ($ln in (Get-LogLines $disp)) {
    $m = $rxOnTrade.Match($ln.Msg)
    if ($m.Success) {
        $tid = $m.Groups['tid'].Value
        $time = $m.Groups['time'].Value
        # skip day-open snapshot: negative synthetic IDs or midnight timestamp
        if ($tid.StartsWith('-') -or $time -match '0:00:00$') { continue }
        Add-Event $ln.Ts 'FILL' $m.Groups['ticker'].Value $m.Groups['dir'].Value `
            (ConvertTo-Num $m.Groups['price'].Value) (ConvertTo-Num $m.Groups['amt'].Value) `
            $m.Groups['oid'].Value $tid $m.Groups['slot'].Value
        continue
    }
    $m = $rxActive.Match($ln.Msg)
    if ($m.Success) {
        $st = if ($m.Groups['state'].Value -eq 'add to active') { 'ORDER_ACTIVE' } else { 'ORDER_INACTIVE' }
        Add-Event $ln.Ts $st $null $null $null $null $m.Groups['oid'].Value $null $m.Groups['slot'].Value
        continue
    }
}

# --------- Trader_*: user order commands ----------
Get-ChildItem -Path $dayDir -Filter "Trader_*.html" | ForEach-Object {
    $lines = Get-LogLines $_.FullName
    # instrument this trader is bound to
    $ticker = ($lines | Where-Object { $_.Msg -match 'TrySetLayer: Ticker=(?<t>\S+)' } | Select-Object -First 1)
    $tkr = if ($ticker) { ([regex]'Ticker=(?<t>\S+)').Match($ticker.Msg).Groups['t'].Value } else { $_.BaseName }

    $rxSend = [regex]'TradeLogic: SendLimitOrder: Price=(?<price>[\d ,\.]+); Direction=(?<dir>\w+)'
    $rxAmt = [regex]'TradeLogic: GetSendOrderAmount: .*Amount=(?<amt>[\d ,\.]+)'
    $rxCancel = [regex]'TradeLogic: CancelLimitOrders: Count=(?<c>\d+)'

    $pendingSend = $null
    foreach ($ln in $lines) {
        $m = $rxSend.Match($ln.Msg)
        if ($m.Success) { $pendingSend = @{ Ts = $ln.Ts; Price = (ConvertTo-Num $m.Groups['price'].Value); Dir = $m.Groups['dir'].Value }; continue }
        $m = $rxAmt.Match($ln.Msg)
        if ($m.Success -and $pendingSend) {
            Add-Event $pendingSend.Ts 'ORDER_SUBMIT' $tkr $pendingSend.Dir $pendingSend.Price (ConvertTo-Num $m.Groups['amt'].Value) $null $null $null
            $pendingSend = $null
            continue
        }
        $m = $rxCancel.Match($ln.Msg)
        if ($m.Success) { Add-Event $ln.Ts 'ORDER_CANCEL' $tkr $null $null $null $null $null "count=$($m.Groups['c'].Value)" }
    }
}

# --------- Output ----------
$sorted = $events | Sort-Object Ts
Write-Host ""
Write-Host "=== CScalp events for $Date  ($($sorted.Count) events) ===" -ForegroundColor Cyan
$sorted | Format-Table -AutoSize @(
    @{L='Time'; E={$_.Ts}},
    @{L='Event'; E={$_.Kind}},
    @{L='Ticker'; E={$_.Ticker}},
    @{L='Side'; E={$_.Side}},
    @{L='Price'; E={$_.Price}},
    @{L='Qty'; E={$_.Amount}},
    @{L='OrderId'; E={$_.OrderId}},
    @{L='TradeId'; E={$_.TradeId}},
    @{L='Slot/Extra'; E={$_.Extra}}
)

Write-Host "--- summary ---" -ForegroundColor Cyan
$sorted | Group-Object Kind | Sort-Object Name | ForEach-Object { Write-Host ("  {0,-16} {1}" -f $_.Name, $_.Count) }
Write-Host "--- fills by ticker ---" -ForegroundColor Cyan
$sorted | Where-Object Kind -eq 'FILL' | Group-Object Ticker | ForEach-Object {
    $net = ($_.Group | ForEach-Object { if ($_.Side -eq 'Buy') { $_.Amount } else { -$_.Amount } } | Measure-Object -Sum).Sum
    Write-Host ("  {0,-20} fills={1,-4} net={2}" -f $_.Name, $_.Count, $net)
}
