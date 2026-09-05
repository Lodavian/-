$ErrorActionPreference = "Stop"

# Explicit Live launcher. Credentials remain in Windows Credential Manager;
# this file contains only non-sensitive environment selection flags.
$env:FAST_ORDER_SANDBOX = "false"
$env:FAST_ORDER_LIVE_ALLOWED = "true"
$env:FAST_ORDER_MAX_MARKET_QTY = "100"
$env:FAST_ORDER_MAX_MARKET_ESTIMATED_VALUE = "5000"
$env:FAST_ORDER_MAX_DAILY_GROSS_NOTIONAL = "25000"
$env:FAST_ORDER_MAX_OPEN_ORDERS = "1"

& (Join-Path $PSScriptRoot "run.ps1")
