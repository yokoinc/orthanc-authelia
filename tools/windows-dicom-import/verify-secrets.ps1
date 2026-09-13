# verify-secrets.ps1
#
# Shows the fingerprints (length + start + end) of the secrets stored in
# config.secrets.dpapi.json. Lets you check "I did type the right passwords"
# without typing them again, and without exposing the whole secrets in the terminal.
#
# Changes nothing. If a secret cannot be decrypted (wrong Windows session,
# corrupted file), this script is also the one that will say so.

$ErrorActionPreference = 'Stop'

$scriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$secretsPath = Join-Path $scriptDir 'config.secrets.dpapi.json'

if (-not (Test-Path $secretsPath)) {
    Write-Host "File not found: $secretsPath" -ForegroundColor Red
    Write-Host "Run setup-secrets.ps1 first." -ForegroundColor Yellow
    exit 1
}

function Unprotect-DpapiString {
    param([string]$Encrypted)
    if ([string]::IsNullOrWhiteSpace($Encrypted)) { return $null }
    try {
        $secure = ConvertTo-SecureString -String $Encrypted -ErrorAction Stop
        $cred = New-Object System.Management.Automation.PSCredential('x', $secure)
        return $cred.GetNetworkCredential().Password
    } catch {
        return '<<decryption failed: wrong Windows session or corrupted file>>'
    }
}

function Get-Fingerprint {
    param([string]$Plain)
    if ([string]::IsNullOrEmpty($Plain)) { return '(empty / not set)' }
    if ($Plain.StartsWith('<<decryption failed')) { return $Plain }
    $len = $Plain.Length
    if ($len -le 8) { return "length=$len, content hidden (too short for a fingerprint)" }
    $first = $Plain.Substring(0, 4)
    $last  = $Plain.Substring($len - 4, 4)
    return "length=$len, start=$first... end=...$last"
}

$secrets = Get-Content $secretsPath -Raw -Encoding UTF8 | ConvertFrom-Json

Write-Host ''
Write-Host '=== DPAPI secrets check ===' -ForegroundColor Cyan
Write-Host "File: $secretsPath"
Write-Host ''

$fields = @(
    @{ Key = 'orthancPassword';      Label = 'Orthanc password           ' }
    @{ Key = 'cfAccessClientId';     Label = 'CF-Access-Client-Id        ' }
    @{ Key = 'cfAccessClientSecret'; Label = 'CF-Access-Client-Secret    ' }
)

foreach ($f in $fields) {
    $enc = $secrets.($f.Key)
    $plain = Unprotect-DpapiString $enc
    $fp = Get-Fingerprint $plain
    Write-Host "$($f.Label): " -NoNewline -ForegroundColor Yellow
    Write-Host $fp
}

Write-Host ''
Write-Host 'If a fingerprint does not match what you expected, run setup-secrets.ps1 again.' -ForegroundColor DarkGray
Write-Host ''
