# import-dicom.ps1
#
# Comportement: detection automatique du mode au lancement.
#
# MODE IMPORT (un CD/DVD est insere) :
#   1. Copie tous les fichiers DICOM (detection par signature 'DICM' a l'offset 128)
#      dans C:\DICOM-Import\<date>_<heure>\ en preservant l'arborescence
#   2. Ouvre le dossier de destination + ejecte le CD
#   3. Upload chaque fichier DICOM individuellement vers Orthanc via REST
#      (un par un pour rester sous la limite Cloudflare 100MB par requete)
#   4. Les echecs sont logges dans _failed-files.txt pour retry ulterieur
#
# MODE RETRY (aucun CD insere) :
#   1. Cherche le dossier d'import le plus recent contenant un _failed-files.txt
#   2. Archive l'ancienne liste, retente l'upload de chaque fichier
#   3. Les nouveaux echecs (s'il en reste) ecrivent un nouveau _failed-files.txt
#
# Si ni CD ni dossier avec failed-files trouves -> erreur explicite.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# --- Trap global: en cas d'exception non capturee, on ferme proprement la fenetre
#     de progression et on affiche un popup d'erreur avant de sortir.
trap {
    try { Close-Status } catch { }
    try {
        [System.Windows.Forms.MessageBox]::Show(
            "Erreur fatale non geree:`r`n$($_.Exception.Message)`r`n`r`nStack:`r`n$($_.ScriptStackTrace)",
            'Import DICOM', 'OK', 'Error'
        ) | Out-Null
    } catch { }
    exit 1
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- Fenetre de progression (suit la copie puis l'upload en temps reel)
$script:cancelRequested = $false

$statusForm = New-Object System.Windows.Forms.Form
$statusForm.Text = 'Import DICOM en cours'
$statusForm.Size = New-Object System.Drawing.Size(460, 240)
$statusForm.StartPosition = 'CenterScreen'
$statusForm.FormBorderStyle = 'FixedSingle'
$statusForm.MinimizeBox = $true
$statusForm.MaximizeBox = $false
$statusForm.ControlBox = $true    # X disponible -> declenche l'annulation

# Fermeture par le X = annulation propre (la boucle upload sortira au prochain
# tour). On empeche la fermeture immediate pour eviter de tuer le script en
# plein milieu d'un upload.
$statusForm.Add_FormClosing({
    param($s, $e)
    if (-not $script:cancelRequested) {
        $script:cancelRequested = $true
        if ($phaseLabel) { $phaseLabel.Text = 'Annulation demandee, finalisation...' }
        $e.Cancel = $true   # ne pas fermer maintenant, laisser la boucle finir proprement
    }
})

$phaseLabel = New-Object System.Windows.Forms.Label
$phaseLabel.Location = New-Object System.Drawing.Point(15, 15)
$phaseLabel.Size = New-Object System.Drawing.Size(420, 24)
$phaseLabel.Font = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
$phaseLabel.Text = 'Initialisation...'

$counterLabel = New-Object System.Windows.Forms.Label
$counterLabel.Location = New-Object System.Drawing.Point(15, 44)
$counterLabel.Size = New-Object System.Drawing.Size(420, 22)
$counterLabel.Font = New-Object System.Drawing.Font('Segoe UI', 9)

$progressBar = New-Object System.Windows.Forms.ProgressBar
$progressBar.Location = New-Object System.Drawing.Point(15, 72)
$progressBar.Size = New-Object System.Drawing.Size(420, 22)
$progressBar.Style = 'Marquee'
$progressBar.MarqueeAnimationSpeed = 30

$detailsLabel = New-Object System.Windows.Forms.Label
$detailsLabel.Location = New-Object System.Drawing.Point(15, 105)
$detailsLabel.Size = New-Object System.Drawing.Size(420, 40)
$detailsLabel.ForeColor = [System.Drawing.Color]::Gray
$detailsLabel.Font = New-Object System.Drawing.Font('Segoe UI', 8)
$detailsLabel.Text = ''

$stopButton = New-Object System.Windows.Forms.Button
$stopButton.Location = New-Object System.Drawing.Point(335, 155)
$stopButton.Size = New-Object System.Drawing.Size(100, 28)
$stopButton.Text = 'Arreter'
$stopButton.Add_Click({
    if (-not $script:cancelRequested) {
        $script:cancelRequested = $true
        $stopButton.Enabled = $false
        $stopButton.Text = 'Annulation...'
        if ($phaseLabel) { $phaseLabel.Text = 'Annulation demandee, finalisation...' }
    }
})

$statusForm.Controls.AddRange(@($phaseLabel, $counterLabel, $progressBar, $detailsLabel, $stopButton))
$statusForm.Show() | Out-Null
[System.Windows.Forms.Application]::DoEvents()

function Update-Status {
    param(
        [string]$Phase,
        [int]$Current = -1,
        [int]$Max = -1,
        [string]$Counter = '',
        [string]$Details = ''
    )
    if ($Phase) { $phaseLabel.Text = $Phase }
    if ($Counter) { $counterLabel.Text = $Counter }
    if ($Max -gt 0) {
        if ($progressBar.Style -ne 'Continuous') { $progressBar.Style = 'Continuous' }
        $progressBar.Maximum = $Max
        $progressBar.Value = [Math]::Max(0, [Math]::Min($Current, $Max))
    } elseif ($Max -eq 0) {
        if ($progressBar.Style -ne 'Marquee') { $progressBar.Style = 'Marquee' }
    }
    if ($PSBoundParameters.ContainsKey('Details')) { $detailsLabel.Text = $Details }
    [System.Windows.Forms.Application]::DoEvents()
}

function Close-Status {
    # Marque cancel pour que le handler FormClosing laisse passer la fermeture
    # (sinon il intercepte le Close en demandant l'annulation -> dead-lock visuel
    # a la fin du script).
    $script:cancelRequested = $true
    if ($statusForm -and -not $statusForm.IsDisposed) {
        $statusForm.Close()
        $statusForm.Dispose()
    }
}

function Show-Box {
    param([string]$Message, [string]$Title = 'Import DICOM', [string]$Icon = 'Information')
    [System.Windows.Forms.MessageBox]::Show($Message, $Title, 'OK', $Icon) | Out-Null
}

function Fail {
    param([string]$Message)
    Close-Status
    Show-Box -Message $Message -Icon 'Error'
    exit 1
}

# --- Charge config
$configPath = Join-Path $scriptDir 'config.json'
if (-not (Test-Path $configPath)) {
    Fail "config.json introuvable a $configPath"
}
try {
    $config = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    Fail "config.json invalide: $($_.Exception.Message)"
}

$importBase  = $config.localFolder
$orthancUrl  = $config.orthancUrl.TrimEnd('/')
$orthancUser = $config.orthancUser

# --- Charge secrets chiffres DPAPI (si presents). Fallback sur les champs
# plain-text de config.json pour retro-compat. setup-secrets.ps1 cree le
# fichier chiffre; une fois en place, supprime les 3 champs plain-text de
# config.json (orthancPassword, cfAccessClientId, cfAccessClientSecret).
function Unprotect-DpapiString {
    param([Parameter(Mandatory=$false)][string]$EncryptedString)
    if ([string]::IsNullOrWhiteSpace($EncryptedString)) { return '' }
    try {
        $secure = ConvertTo-SecureString -String $EncryptedString -ErrorAction Stop
        # Astuce PSCredential pour extraire la chaine en clair sans Marshal direct
        $cred = New-Object System.Management.Automation.PSCredential('x', $secure)
        return $cred.GetNetworkCredential().Password
    } catch {
        return ''
    }
}

$secretsPath = Join-Path $scriptDir 'config.secrets.dpapi.json'
$orthancPwd     = ''
$cfClientId     = ''
$cfClientSecret = ''

if (Test-Path $secretsPath) {
    try {
        $secrets = Get-Content $secretsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $orthancPwd     = Unprotect-DpapiString $secrets.orthancPassword
        $cfClientId     = Unprotect-DpapiString $secrets.cfAccessClientId
        $cfClientSecret = Unprotect-DpapiString $secrets.cfAccessClientSecret
    } catch {
        Fail "config.secrets.dpapi.json invalide ou non dechiffrable: $($_.Exception.Message)`r`nRelance setup-secrets.ps1."
    }
}

# Retro-compat: si config.json contient encore les champs plain-text et que la
# version DPAPI n'a rien donne, on retombe dessus. Permet de migrer sans tout
# casser. A supprimer une fois la migration validee.
if (-not $orthancPwd     -and $config.PSObject.Properties['orthancPassword'])     { $orthancPwd     = $config.orthancPassword }
if (-not $cfClientId     -and $config.PSObject.Properties['cfAccessClientId'])     { $cfClientId     = $config.cfAccessClientId }
if (-not $cfClientSecret -and $config.PSObject.Properties['cfAccessClientSecret']) { $cfClientSecret = $config.cfAccessClientSecret }

# --- Detection du mode: import depuis CD, ou retry des fichiers echoues precedemment
# Comportement:
#   - CD insere -> mode 'import' (scan, copie, eject, upload)
#   - Pas de CD mais un _failed-files.txt existe -> mode 'retry' (retente les uploads)
#   - Ni CD ni _failed-files.txt -> echec explicite
Update-Status -Phase 'Recherche d''un CD/DVD insere...' -Max 0
$drive = Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=5' |
    Where-Object { $_.Size -gt 0 } |
    Select-Object -First 1

$mode = $null
$srcRoot = ''
$ejectMsg = ''
$scanned = 0

# Declaree pour les DEUX modes (import et retry) : le resume final la lit sans
# savoir par quelle branche on est passe.
$unreadable = New-Object System.Collections.Generic.List[string]

if ($drive) {
    # ---------- MODE IMPORT ----------
    $mode = 'import'
    $srcRoot = $drive.DeviceID + '\'
    Update-Status -Phase "Lecteur detecte: $($drive.DeviceID)" -Details "Volume: $($drive.VolumeName)"

    # Cree le dossier de destination
    $timestamp = Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'
    $dst = Join-Path $importBase $timestamp
    New-Item -ItemType Directory -Path $dst -Force | Out-Null

    # Pre-scan rapide: lister les fichiers et sommer leurs tailles. On ne lit
    # PAS le contenu (juste la metadata du TOC ISO du CD), donc c'est rapide
    # (~quelques secondes meme sur un gros CD). Permet d'afficher une vraie
    # barre de progression en MB pendant la phase 1, au lieu du Marquee.
    Update-Status -Phase 'PHASE 1/2 : Indexation du CD (calcul du total)...' -Max 0 -Details ''
    $allFiles = @(Get-ChildItem -Path $srcRoot -Recurse -File -Force -ErrorAction SilentlyContinue)
    $totalBytes = ($allFiles | Measure-Object -Sum Length).Sum
    if (-not $totalBytes) { $totalBytes = 1 }   # safety: evite div/0 si CD vide
    $totalMB = [Math]::Round($totalBytes / 1MB, 0)

    # Scan + copie des fichiers DICOM (magic 'DICM' a l'offset 128)
    Update-Status -Phase 'PHASE 1/2 : Copie locale (scan + copie DICOM)...' -Current 0 -Max 100 -Counter "0 / $totalMB MB - 0 DICOM" -Details ''
    $dicomFiles = New-Object System.Collections.Generic.List[string]
    $bytesScanned = 0

    # Fichiers que le lecteur n'a pas reussi a lire (secteur abime, CD raye ou
    # sale). $ErrorActionPreference vaut 'Stop' et un trap global attrape tout :
    # sans le try/catch ci-dessous, UNE erreur CRC sur un seul fichier tuait
    # l'import entier -- « Erreur de donnees (controle de redondance cyclique) »
    # ligne 281 -- et faisait perdre aussi les fichiers deja copies et tous ceux
    # qui restaient a lire. On note, on continue, et on le dit a la fin.
    #
    # Ces fichiers ne doivent JAMAIS disparaitre en silence : un import qui
    # annonce « termine » en ayant laisse trois coupes en arriere serait bien
    # pire qu'un import qui echoue franchement.

    foreach ($file in $allFiles) {
        $scanned++
        $bytesScanned += $file.Length

        # Update UI tous les 10 fichiers scannes (evite de spammer l'UI).
        # On n'affiche PAS le nom du fichier dans Details (PII potentiel).
        if ($scanned % 10 -eq 0) {
            $pct = [int](100 * $bytesScanned / $totalBytes)
            $mbScanned = [Math]::Round($bytesScanned / 1MB, 0)
            Update-Status -Current $pct -Max 100 -Counter "$mbScanned / $totalMB MB - $($dicomFiles.Count) DICOM" -Details ''
        }

        if ($file.Length -lt 132) { continue }

        $isDicom = $false
        $headerUnreadable = $false
        $fs = $null
        try {
            $fs = [System.IO.File]::OpenRead($file.FullName)
            $fs.Seek(128, 'Begin') | Out-Null
            $buf = New-Object byte[] 4
            $null = $fs.Read($buf, 0, 4)
            if ($buf[0] -eq 0x44 -and $buf[1] -eq 0x49 -and $buf[2] -eq 0x43 -and $buf[3] -eq 0x4D) {
                $isDicom = $true
            }
        } catch [System.IO.IOException] {
            # Le secteur qui porte l'en-tete est illisible. On ne sait donc PAS
            # si c'est une image DICOM. L'ancien catch vide le classait
            # implicitement en « pas du DICOM » et le fichier disparaissait sans
            # un mot : sur un disque abime, c'est exactement le cas ou il faut
            # parler.
            $headerUnreadable = $true
            $unreadable.Add("$($file.FullName)  [en-tete illisible] $($_.Exception.Message)")
        } catch { }
        finally { if ($fs) { $fs.Close() } }
        if ($headerUnreadable) { continue }

        if ($isDicom) {
            $rel = $file.FullName.Substring($srcRoot.Length)
            $target = Join-Path $dst $rel
            $targetDir = Split-Path $target -Parent
            if (-not (Test-Path $targetDir)) {
                New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
            }
            # Une seule tentative. Pas de relecture : face a un secteur abime,
            # le pilote Windows insiste deja tout seul 30 s a 2 min avant de
            # rendre la main, et chaque essai supplementaire rajoute autant.
            # Un import se fait entre deux patients -- illisible, c'est illisible,
            # on passe. Ce qui est perdu est compte et annonce a la fin.
            $copyOk = $false
            $lastErr = ''
            try {
                Copy-Item -LiteralPath $file.FullName -Destination $target -Force
                $copyOk = $true
            } catch {
                $lastErr = $_.Exception.Message
                # Copy-Item laisse un fichier tronque derriere lui quand il meurt
                # en cours de route : on l'efface, un reste partiel ne doit jamais
                # partir vers Orthanc.
                Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
            }

            if (-not $copyOk) {
                $unreadable.Add("$($file.FullName)  [copie impossible] $lastErr")
                # Rafraichissement immediat : sur un disque abime, l'affichage
                # ne bouge qu'un fichier sur dix et donne l'impression d'un
                # blocage. Le compteur d'illisibles, lui, doit se voir tout de
                # suite -- c'est le signe que le disque lache.
                $mbScanned = [Math]::Round($bytesScanned / 1MB, 0)
                Update-Status -Current ([int](100 * $bytesScanned / $totalBytes)) -Max 100 `
                    -Counter "$mbScanned / $totalMB MB - $($dicomFiles.Count) DICOM - $($unreadable.Count) illisible(s)" `
                    -Details ''
                continue
            }

            # Les fichiers d'un CD heritent de l'attribut read-only et avec lui
            # Invoke-RestMethod -InFile echoue plus tard avec "Acces refuse" sur
            # certains setups PowerShell. On le clear systematiquement ici.
            try { (Get-Item -LiteralPath $target).IsReadOnly = $false } catch { }
            $dicomFiles.Add($target)
        }
    }

    $copied = $dicomFiles.Count
    $bilanCopie = "PHASE 1/2 : Copie locale terminee ($copied fichier(s))"
    if ($unreadable.Count -gt 0) { $bilanCopie += " - $($unreadable.Count) illisible(s)" }
    Update-Status -Phase $bilanCopie -Current 100 -Max 100 `
        -Counter "$totalMB / $totalMB MB - $copied DICOM" -Details ''

    # Trace sur disque des fichiers illisibles. Le dossier date n'est efface en
    # fin de course que si TOUT a reussi ; en presence d'illisibles on le garde,
    # ce fichier avec, pour pouvoir reprendre le disque plus tard.
    if ($unreadable.Count -gt 0) {
        $unreadablePath = Join-Path $dst '_unreadable-files.txt'
        Set-Content -LiteralPath $unreadablePath -Value $unreadable -Encoding UTF8
    }

    # Copie aussi DICOMDIR s'il existe (pratique en local, Orthanc l'ignore).
    # Lui aussi vit sur le disque abime : un CRC ici ne doit pas tuer l'import
    # alors que les images, elles, sont deja copiees.
    $dicomdir = Join-Path $srcRoot 'DICOMDIR'
    if (Test-Path $dicomdir) {
        try {
            Copy-Item -LiteralPath $dicomdir -Destination (Join-Path $dst 'DICOMDIR') -Force
        } catch {
            $unreadable.Add("$dicomdir  [copie impossible] $($_.Exception.Message)")
        }
    }

    if ($copied -eq 0) {
        Remove-Item -Path $dst -Recurse -Force -ErrorAction SilentlyContinue
        Fail "Aucun fichier DICOM trouve sur $srcRoot ($scanned fichiers scannes)."
    }

    # Ejecte le CD/DVD - on a fini de lire dessus.
    try {
        $shell = New-Object -ComObject Shell.Application
        $shell.Namespace(17).ParseName($drive.DeviceID).InvokeVerb('Eject')
        $ejectMsg = "CD ejecte ($($drive.DeviceID))."
    } catch {
        $ejectMsg = "Ejection du CD echouee: $($_.Exception.Message)"
    }

} else {
    # ---------- MODE RETRY ----------
    # Pas de CD insere -> on cherche le dossier d'import le plus recent qui contient
    # un _failed-files.txt non vide, et on retente les uploads.
    Update-Status -Phase 'Pas de CD - recherche d''un retry possible...' -Max 0

    $candidate = Get-ChildItem -Path $importBase -Directory -ErrorAction SilentlyContinue |
        Where-Object {
            $f = Join-Path $_.FullName '_failed-files.txt'
            (Test-Path $f) -and ((Get-Item $f).Length -gt 0)
        } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if (-not $candidate) {
        Fail "Aucun CD insere, et aucun dossier dans $importBase ne contient de fichiers a retenter."
    }

    $mode = 'retry'
    $dst = $candidate.FullName
    $srcRoot = "(retry du dossier $($candidate.Name))"
    $previousFailedPath = Join-Path $dst '_failed-files.txt'

    # Lit la liste, ne garde que les fichiers qui existent toujours sur disque
    $dicomFiles = New-Object System.Collections.Generic.List[string]
    Get-Content -Path $previousFailedPath -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -and (Test-Path -LiteralPath $line)) {
            $dicomFiles.Add($line)
        }
    }
    $copied = $dicomFiles.Count

    if ($copied -eq 0) {
        Fail "_failed-files.txt trouve mais aucun fichier listing ne existe encore sur disque: $previousFailedPath"
    }

    # Archive l'ancienne liste pour historique (les nouveaux echecs ecriront un fichier vierge)
    $archivedPath = Join-Path $dst ("_failed-files.txt.previous-" + (Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'))
    Move-Item -LiteralPath $previousFailedPath -Destination $archivedPath -Force

    Update-Status -Phase "PHASE 2/2 : Mode retry ($copied fichier(s) a retenter)" -Current 0 -Max $copied -Counter "0 / $copied" -Details "Source: $($candidate.Name)"
}

# --- Upload Orthanc fichier par fichier
# On evite le ZIP global pour ne pas depasser la limite Cloudflare (100MB par requete sur Free/Pro).
# Chaque .dcm est envoye individuellement avec un petit delai pour respecter le rate limit nginx (2r/s).
$pair = "${orthancUser}:${orthancPwd}"
$basicAuth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
$headers = @{ Authorization = "Basic $basicAuth" }

# Headers Cloudflare Access (verifies par CF Edge AVANT meme d'atteindre nginx)
if ($cfClientId -and $cfClientSecret) {
    $headers['CF-Access-Client-Id']     = $cfClientId
    $headers['CF-Access-Client-Secret'] = $cfClientSecret
}

$uploaded = 0
$failed = 0
$firstError = $null
$errorLog = Join-Path $dst '_upload-errors.log'   # log detaille des echecs
$failedListPath = Join-Path $dst '_failed-files.txt'  # liste des paths echoues (pour retry)
$statusCounts = @{}   # ex: { 403 = 12; 502 = 3 } pour detecter un pattern global

$total = $dicomFiles.Count

# Clear read-only sur tous les fichiers a uploader (defense en profondeur,
# couvre aussi le mode RETRY ou les fichiers viennent d'un ancien import
# qui n'avait pas le fix de copie ci-dessus). Sans ca, Invoke-RestMethod
# -InFile echoue avec "Acces refuse" sur des fichiers herites d'un CD.
Update-Status -Phase 'PHASE 2/2 : Preparation upload (clear read-only)...' -Max 0
foreach ($p in $dicomFiles) {
    try { (Get-Item -LiteralPath $p).IsReadOnly = $false } catch { }
}

Update-Status -Phase 'PHASE 2/2 : Upload Orthanc...' -Current 0 -Max $total -Counter "0 / $total" -Details ''

$index = 0
foreach ($f in $dicomFiles) {
    # Laisse la form traiter les clicks bouton / X avant chaque iteration
    [System.Windows.Forms.Application]::DoEvents()
    # Annulation demandee via bouton "Arreter" ou X: sortir proprement et
    # persister les fichiers PAS ENCORE essayes dans _failed-files.txt (sinon
    # ils seraient perdus puisqu'on a deja move l'ancien fichier en archive).
    if ($script:cancelRequested) {
        $remaining = $dicomFiles | Select-Object -Skip $index
        foreach ($r in $remaining) {
            try { Add-Content -Path $failedListPath -Value $r -Encoding UTF8 } catch { }
        }
        Update-Status -Phase 'Upload interrompu par l''utilisateur' -Details ''
        break
    }
    $index++
    $retries = 0
    $maxRetries = 3
    $success = $false
    while (-not $success) {
        try {
            Invoke-RestMethod -Uri "$orthancUrl/instances" `
                              -Method Post `
                              -Headers $headers `
                              -InFile $f `
                              -ContentType 'application/dicom' `
                              -TimeoutSec 120 | Out-Null
            $uploaded++
            $success = $true
            # Le fichier est en securite dans Orthanc -> on supprime la copie
            # locale. Si l'upload est interrompu plus tard, le re-run en mode
            # RETRY ne re-tentera que les fichiers encore presents (les
            # uploades sont dedupes par SOPInstanceUID cote Orthanc de toute
            # facon, donc pas de risque de doublon meme en cas de redondance).
            try { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue } catch { }
        } catch {
            $statusCode = $null
            if ($_.Exception.Response) {
                try { $statusCode = [int]$_.Exception.Response.StatusCode } catch { }
            }
            # 429 (rate limit) ou 5xx (erreur serveur transitoire) -> backoff exponentiel + retry
            $isRetryable = ($statusCode -eq 429) -or ($statusCode -ge 500 -and $statusCode -lt 600)
            if ($isRetryable -and $retries -lt $maxRetries) {
                Start-Sleep -Seconds ([Math]::Pow(2, $retries))
                $retries++
                continue
            }
            # Echec definitif: log + comptage
            $failed++
            $name = Split-Path $f -Leaf
            $errMsg = $_.Exception.Message
            $codeStr = if ($statusCode) { "HTTP $statusCode" } else { 'erreur reseau' }

            # Ligne dans le log detaille (UTF8 pour gerer les filenames non-ASCII)
            $stamp = (Get-Date).ToString('HH:mm:ss')
            Add-Content -Path $errorLog -Value "[$stamp] $codeStr - $f`r`n         -> $errMsg`r`n" -Encoding UTF8
            # Liste des paths echoues (1 par ligne, format simple pour retry)
            Add-Content -Path $failedListPath -Value $f -Encoding UTF8

            # Premier echec memorise pour affichage rapide
            if (-not $firstError) {
                $firstError = "${name} (${codeStr}): $errMsg"
            }
            # Statistique par code HTTP (detecter un pb global ex: tous en 403)
            $key = if ($statusCode) { "$statusCode" } else { 'net' }
            if ($statusCounts.ContainsKey($key)) { $statusCounts[$key]++ } else { $statusCounts[$key] = 1 }

            $success = $true   # sort de la boucle while (echec definitif)
        }
    }
    # Update UI - on affiche compteurs uniquement (pas le filename, PII potentiel)
    $done = $uploaded + $failed
    $detail = if ($failed -gt 0) { "$uploaded reussis, $failed echecs" } else { "$uploaded reussis" }
    Update-Status -Current $done -Max $total -Counter "$done / $total" -Details $detail

    # Petit delai pour rester sous le rate limit nginx (2r/s sustained)
    Start-Sleep -Milliseconds 500
}

$pushOk = ($uploaded -gt 0 -and $failed -eq 0)
$pushMsg = "$uploaded/$($dicomFiles.Count) fichier(s) DICOM uploades vers Orthanc."

# Nettoyage des dossiers dates affectes:
#   - Si TOUT a reussi (pas d'echec, pas d'annulation) -> on rm -rf le(s)
#     dossier(s) date(s) qui contenai(en)t les fichiers uploades. Comme on
#     vient deja de supprimer chaque fichier apres son upload reussi (cf.
#     Remove-Item dans la boucle), il reste juste les coquilles vides +
#     DICOMDIR + d'eventuels fichiers non-DICOM.
#   - Si echecs OU annulation -> on garde le dossier intact (les fichiers
#     restants + _failed-files.txt + _upload-errors.log) pour permettre un
#     re-run propre.
# On supporte le cas "combined retry" ou _failed-files.txt contient des
# paths qui pointent vers plusieurs dossiers dates differents: on collecte
# l'ensemble des dossiers dates effectivement touches.
# $unreadable.Count : le menage effacerait _unreadable-files.txt avec le
# dossier. Tant qu'il reste des fichiers illisibles, on garde tout sur place --
# c'est la trace de ce qui manque, et elle doit survivre a un import « reussi ».
if ($pushOk -and -not $script:cancelRequested -and $unreadable.Count -eq 0) {
    $importBaseTrimmed = $importBase.TrimEnd('\','/')
    $affectedDatedFolders = $dicomFiles | ForEach-Object {
        # Remonte jusqu'au dossier immediat sous $importBase
        $cur = Split-Path $_ -Parent
        while ($cur -and $cur -ne $importBaseTrimmed) {
            $parent = Split-Path $cur -Parent
            if (-not $parent -or $parent -eq $cur) { break }
            if ($parent -eq $importBaseTrimmed) { break }
            $cur = $parent
        }
        if ($cur -ne $importBaseTrimmed) { $cur }
    } | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Sort-Object -Unique

    foreach ($folder in $affectedDatedFolders) {
        try {
            Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
        } catch { }
    }
    if ($affectedDatedFolders) {
        $pushMsg += "`r`n$($affectedDatedFolders.Count) dossier(s) local(aux) supprime(s) apres upload reussi."
    }
}
if ($failed -gt 0) {
    # Resume des codes HTTP pour reperer un probleme systemique (ex: tous en 403 = pb auth CF)
    $codesSummary = ($statusCounts.GetEnumerator() | ForEach-Object { "$($_.Value)x $($_.Key)" }) -join ', '
    $pushMsg += "`r`n$failed echec(s) [$codesSummary]"
    $pushMsg += "`r`nPremier: $firstError"
    $pushMsg += "`r`nLog detaille: $errorLog"
    $pushMsg += "`r`nListe a rejouer: $failedListPath"
}

# --- Resume
Close-Status   # ferme la fenetre de progression avant d'afficher le popup final

$lines = New-Object System.Collections.Generic.List[string]
if ($mode -eq 'import') {
    $lines.Add("Mode: Import CD")
    $lines.Add("Source: $srcRoot")
    $lines.Add("Destination: $dst")
    $lines.Add("$copied fichier(s) DICOM copie(s) (sur $scanned scannes).")
    if ($ejectMsg) { $lines.Add($ejectMsg) }
} else {
    $lines.Add("Mode: Retry des fichiers echoues")
    $lines.Add("Dossier: $dst")
    $lines.Add("$copied fichier(s) a retenter.")
}
if ($unreadable.Count -gt 0) {
    # Le fait, sans conseil ni consigne : l'operateur sait quoi faire d'un CD
    # abime, et l'import se fait entre deux patients.
    $lines.Add("$($unreadable.Count) fichier(s) illisible(s) sur le disque, non importe(s).")
    $lines.Add("Liste : $(Join-Path $dst '_unreadable-files.txt')")
}
$lines.Add($pushMsg)
$summary = $lines -join "`r`n"

Show-Box -Message $summary -Icon $(if ($pushOk -and $unreadable.Count -eq 0) { 'Information' } else { 'Warning' })
