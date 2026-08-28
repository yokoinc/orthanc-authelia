# verify-secrets.ps1
#
# Affiche les fingerprints (longueur + debut + fin) des secrets stockes dans
# config.secrets.dpapi.json. Permet de verifier "j'ai bien tape les bons mdp"
# sans avoir a re-saisir, et sans exposer les secrets entiers dans le terminal.
#
# Ne modifie rien. Si un secret n'arrive pas a se dechiffrer (mauvaise session
# Windows, fichier corrompu) c'est aussi ce script qui le dira.

$ErrorActionPreference = 'Stop'

$scriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$secretsPath = Join-Path $scriptDir 'config.secrets.dpapi.json'

if (-not (Test-Path $secretsPath)) {
    Write-Host "Fichier introuvable: $secretsPath" -ForegroundColor Red
    Write-Host "Lance d'abord setup-secrets.ps1." -ForegroundColor Yellow
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
        return '<<echec dechiffrement: pas la bonne session Windows ou fichier corrompu>>'
    }
}

function Get-Fingerprint {
    param([string]$Plain)
    if ([string]::IsNullOrEmpty($Plain)) { return '(vide / non defini)' }
    if ($Plain.StartsWith('<<echec')) { return $Plain }
    $len = $Plain.Length
    if ($len -le 8) { return "longueur=$len, contenu masque (trop court pour fingerprint)" }
    $first = $Plain.Substring(0, 4)
    $last  = $Plain.Substring($len - 4, 4)
    return "longueur=$len, debut=$first... fin=...$last"
}

$secrets = Get-Content $secretsPath -Raw -Encoding UTF8 | ConvertFrom-Json

Write-Host ''
Write-Host '=== Verification des secrets DPAPI ===' -ForegroundColor Cyan
Write-Host "Fichier: $secretsPath"
Write-Host ''

$fields = @(
    @{ Key = 'orthancPassword';      Label = 'Mot de passe Orthanc      ' }
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
Write-Host 'Si un fingerprint ne correspond pas a ce que tu attendais, relance setup-secrets.ps1.' -ForegroundColor DarkGray
Write-Host ''
