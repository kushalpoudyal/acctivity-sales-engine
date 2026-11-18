<#
.SYNOPSIS
  Hands the rep's Acctivity (test) access token from the tray app's token cache to the local Stalktivity API.

.DESCRIPTION
  Runs on the Windows host (not in Docker), as the rep. The Acctivity tray app keeps its MSAL token cache
  DPAPI-encrypted for the current user at %LOCALAPPDATA%\Acctivity\creds\test\.token. This script decrypts it,
  takes ONLY the newest access token and sends it to PUT <ApiUrl>/api/v1/demo/rep-login, where the API keeps it in
  memory. It never reads out, prints, stores or sends the refresh token: redeeming it elsewhere would sign the tray
  app out (Auth0 refresh token rotation). The access token is never printed or written to disk.

  If the access token has expired, open the Acctivity tray app (test) so it refreshes, then run this again.

.EXAMPLE
  pwsh -File Hack_A_API\tools\Send-RepLogin.ps1
.EXAMPLE
  pwsh -File Hack_A_API\tools\Send-RepLogin.ps1 -DryRun   # only reports the expiry, sends nothing
#>
[CmdletBinding()]
param(
    [string]$ApiUrl = "http://127.0.0.1:9001",
    [string]$CachePath,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$RefreshHint = "open the Acctivity tray app (test) to refresh"

function Find-TokenCache {
    if ($CachePath) { return $CachePath }
    $logical = Join-Path $env:LOCALAPPDATA "Acctivity\creds\test\.token"
    if (Test-Path -LiteralPath $logical) { return $logical }
    # MSIX installs may redirect %LOCALAPPDATA% writes into the package's LocalCache.
    $packages = Join-Path $env:LOCALAPPDATA "Packages"
    if (Test-Path -LiteralPath $packages) {
        foreach ($package in Get-ChildItem -LiteralPath $packages -Directory) {
            $candidate = Join-Path $package.FullName "LocalCache\Local\Acctivity\creds\test\.token"
            if (Test-Path -LiteralPath $candidate) { return $candidate }
        }
    }
    return $logical
}

$path = Find-TokenCache
if (-not (Test-Path -LiteralPath $path)) {
    Write-Error "No Acctivity (test) token cache found. Sign in to the Acctivity tray app on the test environment, or paste a token in the Demo tab."
}

try { Add-Type -AssemblyName System.Security } catch { }
$encrypted = [System.IO.File]::ReadAllBytes($path)
$plain = [System.Security.Cryptography.ProtectedData]::Unprotect(
    $encrypted, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
try {
    $cache = [System.Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json
} finally {
    [Array]::Clear($plain, 0, $plain.Length)
}

# Only the AccessToken section is read; the cache's other sections are left untouched.
$newest = $null
if ($cache.AccessToken) {
    foreach ($entry in $cache.AccessToken.PSObject.Properties.Value) {
        if (-not $entry.secret) { continue }
        $expiresOn = [long]$entry.expires_on
        if (-not $newest -or $expiresOn -gt $newest.ExpiresOn) {
            $newest = [pscustomobject]@{ Token = [string]$entry.secret; ExpiresOn = $expiresOn }
        }
    }
}
$cache = $null
if (-not $newest) {
    Write-Error "The token cache holds no access token. Sign in to the Acctivity tray app (test)."
}

$expiry = [DateTimeOffset]::FromUnixTimeSeconds($newest.ExpiresOn)
if ($expiry -le [DateTimeOffset]::UtcNow.AddSeconds(60)) {
    Write-Error "The rep login expired at $($expiry.ToLocalTime().ToString('g')): $RefreshHint, then run this again."
}
Write-Host "Rep login found; expires $($expiry.ToLocalTime().ToString('g'))."
if ($DryRun) { Write-Host "Dry run: nothing sent."; return }

$body = @{ access_token = $newest.Token; source = "tray" } | ConvertTo-Json -Compress
$newest = $null
try {
    $status = Invoke-RestMethod -Method Put -Uri "$($ApiUrl.TrimEnd('/'))/api/v1/demo/rep-login" `
        -ContentType "application/json" -Body $body
} catch {
    $message = $_.ErrorDetails.Message
    if (-not $message) { $message = $_.Exception.Message }
    Write-Error "The API did not accept the rep login: $message"
} finally {
    $body = $null
}
Write-Host "Sent to the API: status $($status.status), firm $($status.firm_id), expires $($status.expires_at)."
