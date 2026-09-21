# import-dicom.ps1
#
# Behaviour: the mode is detected automatically at launch.
#
# IMPORT MODE (a CD/DVD is inserted):
#   1. Copies every DICOM file (detected by the 'DICM' signature at offset 128)
#      into C:\DICOM-Import\<date>_<time>\, preserving the directory tree
#   2. Opens the destination folder + ejects the CD
#   3. Uploads each DICOM file individually to Orthanc through REST
#      (one at a time, to stay under Cloudflare's 100MB-per-request limit)
#   4. Failures are logged in _failed-files.txt for a later retry
#
# RETRY MODE (no CD inserted):
#   1. Gathers ALL pending folders (non-empty failure list, or files still
#      present): nothing piles up from one run to the next. A file is deleted
#      as soon as its upload succeeds, so whatever remains never left
#      (import interrupted before the upload phase even started).
#   2. Archives the old lists, sends everything, then deletes the empty
#      folders. 3. Remaining failures write a new _failed-files.txt
#
# Neither a CD nor a usable folder -> explicit error.
#
# Messages follow the Windows display language: French on a French Windows,
# English otherwise.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Picks the message matching the Windows display language. Defined before the
# trap below, which also uses it.
$script:isFrench = (Get-UICulture).TwoLetterISOLanguageName -eq 'fr'
function L {
    param([string]$English, [string]$French)
    if ($script:isFrench) { $French } else { $English }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# --- Global trap: on an uncaught exception, close the progress window cleanly
#     and show an error popup before exiting.
trap {
    try { Close-Status } catch { }
    try {
        [System.Windows.Forms.MessageBox]::Show(
            (L "Unhandled fatal error:`r`n$($_.Exception.Message)`r`n`r`nStack:`r`n$($_.ScriptStackTrace)" "Erreur fatale non geree:`r`n$($_.Exception.Message)`r`n`r`nStack:`r`n$($_.ScriptStackTrace)"),
            'Import DICOM', 'OK', 'Error'
        ) | Out-Null
    } catch { }
    exit 1
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- Progress window (follows the copy, then the upload, in real time)
$script:cancelRequested = $false

$statusForm = New-Object System.Windows.Forms.Form
$statusForm.Text = (L 'DICOM import in progress' 'Import DICOM en cours')
$statusForm.Size = New-Object System.Drawing.Size(460, 240)
$statusForm.StartPosition = 'CenterScreen'
$statusForm.FormBorderStyle = 'FixedSingle'
$statusForm.MinimizeBox = $true
$statusForm.MaximizeBox = $false
$statusForm.ControlBox = $true    # X available -> triggers cancellation

# Closing with the X = clean cancellation (the upload loop exits on its next
# iteration). Immediate closing is prevented so the script is not killed in
# the middle of an upload.
$statusForm.Add_FormClosing({
    param($s, $e)
    if (-not $script:cancelRequested) {
        $script:cancelRequested = $true
        if ($phaseLabel) { $phaseLabel.Text = (L 'Cancellation requested, finishing...' 'Annulation demandee, finalisation...') }
        $e.Cancel = $true   # do not close now, let the loop finish cleanly
    }
})

$phaseLabel = New-Object System.Windows.Forms.Label
$phaseLabel.Location = New-Object System.Drawing.Point(15, 15)
$phaseLabel.Size = New-Object System.Drawing.Size(420, 24)
$phaseLabel.Font = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
$phaseLabel.Text = (L 'Initialising...' 'Initialisation...')

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
$stopButton.Text = (L 'Stop' 'Arreter')
$stopButton.Add_Click({
    if (-not $script:cancelRequested) {
        $script:cancelRequested = $true
        $stopButton.Enabled = $false
        $stopButton.Text = (L 'Cancelling...' 'Annulation...')
        if ($phaseLabel) { $phaseLabel.Text = (L 'Cancellation requested, finishing...' 'Annulation demandee, finalisation...') }
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
    # Set cancel so that the FormClosing handler lets the close through
    # (otherwise it intercepts Close by requesting cancellation -> visual
    # dead-lock at the end of the script).
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

# --- Load config
$configPath = Join-Path $scriptDir 'config.json'
if (-not (Test-Path $configPath)) {
    Fail (L "config.json not found at $configPath" "config.json introuvable a $configPath")
}
try {
    $config = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    Fail (L "Invalid config.json: $($_.Exception.Message)" "config.json invalide: $($_.Exception.Message)")
}

$importBase  = $config.localFolder
$orthancUrl  = $config.orthancUrl.TrimEnd('/')
$orthancUser = $config.orthancUser

# --- Load the DPAPI-encrypted secrets (if present). Falls back on the
# plain-text fields of config.json for backward compatibility. setup-secrets.ps1
# creates the encrypted file; once it is in place, delete the 3 plain-text
# fields from config.json (orthancPassword, cfAccessClientId, cfAccessClientSecret).
function Unprotect-DpapiString {
    param([Parameter(Mandatory=$false)][string]$EncryptedString)
    if ([string]::IsNullOrWhiteSpace($EncryptedString)) { return '' }
    try {
        $secure = ConvertTo-SecureString -String $EncryptedString -ErrorAction Stop
        # PSCredential trick to extract the clear string without calling Marshal directly
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
        Fail (L "config.secrets.dpapi.json is invalid or cannot be decrypted: $($_.Exception.Message)`r`nRun setup-secrets.ps1 again." "config.secrets.dpapi.json invalide ou non dechiffrable: $($_.Exception.Message)`r`nRelance setup-secrets.ps1.")
    }
}

# Backward compatibility: if config.json still holds the plain-text fields and
# the DPAPI version yielded nothing, fall back on them. Allows migrating
# without breaking everything. To be removed once the migration is validated.
if (-not $orthancPwd     -and $config.PSObject.Properties['orthancPassword'])     { $orthancPwd     = $config.orthancPassword }
if (-not $cfClientId     -and $config.PSObject.Properties['cfAccessClientId'])     { $cfClientId     = $config.cfAccessClientId }
if (-not $cfClientSecret -and $config.PSObject.Properties['cfAccessClientSecret']) { $cfClientSecret = $config.cfAccessClientSecret }

# --- Mode detection: import from CD, or retry of previously failed files
# Behaviour:
#   - CD inserted -> 'import' mode (scan, copy, eject, upload)
#   - No CD but a _failed-files.txt exists -> 'retry' mode (retries the uploads)
#   - Neither CD nor _failed-files.txt -> explicit failure
Update-Status -Phase (L 'Looking for an inserted CD/DVD...' 'Recherche d''un CD/DVD insere...') -Max 0
$drive = Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=5' |
    Where-Object { $_.Size -gt 0 } |
    Select-Object -First 1

$mode = $null
$srcRoot = ''
$ejectMsg = ''
$scanned = 0

# Declared for BOTH modes (import and retry): the final summary reads it
# without knowing which branch was taken.
$unreadable = New-Object System.Collections.Generic.List[string]

if ($drive) {
    # ---------- IMPORT MODE ----------
    $mode = 'import'
    $srcRoot = $drive.DeviceID + '\'
    Update-Status -Phase (L "Drive detected: $($drive.DeviceID)" "Lecteur detecte: $($drive.DeviceID)") -Details "Volume: $($drive.VolumeName)"

    # Create the destination folder
    $timestamp = Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'
    $dst = Join-Path $importBase $timestamp
    New-Item -ItemType Directory -Path $dst -Force | Out-Null

    # Quick pre-scan: list the files and add up their sizes. The content is NOT
    # read (only the metadata of the CD's ISO TOC), so it is fast (~a few
    # seconds even on a big CD). Allows showing a real progress bar in MB
    # during phase 1, instead of the Marquee.
    Update-Status -Phase (L 'PHASE 1/2: Indexing the CD (computing the total)...' 'PHASE 1/2 : Indexation du CD (calcul du total)...') -Max 0 -Details ''
    $allFiles = @(Get-ChildItem -Path $srcRoot -Recurse -File -Force -ErrorAction SilentlyContinue)
    $totalBytes = ($allFiles | Measure-Object -Sum Length).Sum
    if (-not $totalBytes) { $totalBytes = 1 }   # safety: avoids div/0 on an empty CD
    $totalMB = [Math]::Round($totalBytes / 1MB, 0)

    # Scan + copy of the DICOM files (magic 'DICM' at offset 128)
    Update-Status -Phase (L 'PHASE 1/2: Local copy (scan + DICOM copy)...' 'PHASE 1/2 : Copie locale (scan + copie DICOM)...') -Current 0 -Max 100 -Counter "0 / $totalMB MB - 0 DICOM" -Details ''
    $dicomFiles = New-Object System.Collections.Generic.List[string]
    $bytesScanned = 0

    # Files the drive failed to read (damaged sector, scratched or dirty CD).
    # $ErrorActionPreference is 'Stop' and a global trap catches everything:
    # without the try/catch below, ONE CRC error on a single file killed the
    # whole import -- "Data error (cyclic redundancy check)" at line 281 -- and
    # also lost the files already copied and all those still to be read. We
    # note it, carry on, and say so at the end.
    #
    # These files must NEVER disappear silently: an import that announces
    # "complete" while having left three slices behind would be far worse than
    # an import that fails outright.

    foreach ($file in $allFiles) {
        $scanned++
        $bytesScanned += $file.Length

        # Update the UI every 10 scanned files (avoids spamming the UI).
        # The file name is NOT shown in Details (potential PII).
        if ($scanned % 10 -eq 0) {
            $pct = [int](100 * $bytesScanned / $totalBytes)
            $mbScanned = [Math]::Round($bytesScanned / 1MB, 0)
            Update-Status -Current $pct -Max 100 -Counter "$mbScanned / $totalMB MB - $($dicomFiles.Count) DICOM" -Details ''
        }

        if ($file.Length -lt 132) { continue }
        # DICOMDIR carries the DICM signature too, but it is the CD's index,
        # not an image: Orthanc refuses it, it landed in _failed-files.txt and
        # was sent again on every later run. It is copied on its own below.
        if ($file.Name -eq 'DICOMDIR') { continue }

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
            # The sector holding the header is unreadable. So we do NOT know
            # whether it is a DICOM image. The old empty catch implicitly
            # classed it as "not DICOM" and the file vanished without a word:
            # on a damaged disc, that is exactly the case where we must speak.
            $headerUnreadable = $true
            $unreadable.Add((L "$($file.FullName)  [unreadable header] $($_.Exception.Message)" "$($file.FullName)  [en-tete illisible] $($_.Exception.Message)"))
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
            # A single attempt. No re-read: faced with a damaged sector, the
            # Windows driver already insists on its own for 30 s to 2 min
            # before giving control back, and every extra attempt adds as much.
            # An import happens between two patients -- unreadable is
            # unreadable, move on. What is lost is counted and reported at the end.
            $copyOk = $false
            $lastErr = ''
            try {
                Copy-Item -LiteralPath $file.FullName -Destination $target -Force
                $copyOk = $true
            } catch {
                $lastErr = $_.Exception.Message
                # Copy-Item leaves a truncated file behind when it dies halfway:
                # delete it, a partial leftover must never go to Orthanc.
                Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
            }

            if (-not $copyOk) {
                $unreadable.Add((L "$($file.FullName)  [copy failed] $lastErr" "$($file.FullName)  [copie impossible] $lastErr"))
                # Immediate refresh: on a damaged disc, the display only moves
                # one file in ten and looks frozen. The unreadable counter, on
                # the other hand, must show at once -- it is the sign that the
                # disc is failing.
                $mbScanned = [Math]::Round($bytesScanned / 1MB, 0)
                Update-Status -Current ([int](100 * $bytesScanned / $totalBytes)) -Max 100 `
                    -Counter (L "$mbScanned / $totalMB MB - $($dicomFiles.Count) DICOM - $($unreadable.Count) unreadable" "$mbScanned / $totalMB MB - $($dicomFiles.Count) DICOM - $($unreadable.Count) illisible(s)") `
                    -Details ''
                continue
            }

            # Files from a CD inherit the read-only attribute, and with it
            # Invoke-RestMethod -InFile later fails with "Access denied" on
            # some PowerShell setups. It is cleared systematically here.
            try { (Get-Item -LiteralPath $target).IsReadOnly = $false } catch { }
            $dicomFiles.Add($target)
        }
    }

    $copied = $dicomFiles.Count
    $bilanCopie = (L "PHASE 1/2: Local copy complete ($copied file(s))" "PHASE 1/2 : Copie locale terminee ($copied fichier(s))")
    if ($unreadable.Count -gt 0) { $bilanCopie += (L " - $($unreadable.Count) unreadable" " - $($unreadable.Count) illisible(s)") }
    Update-Status -Phase $bilanCopie -Current 100 -Max 100 `
        -Counter "$totalMB / $totalMB MB - $copied DICOM" -Details ''

    # On-disk record of the unreadable files. The dated folder is only deleted
    # at the end if EVERYTHING succeeded; when there are unreadable files it is
    # kept, together with this file, so the disc can be picked up again later.
    if ($unreadable.Count -gt 0) {
        $unreadablePath = Join-Path $dst '_unreadable-files.txt'
        Set-Content -LiteralPath $unreadablePath -Value $unreadable -Encoding UTF8
    }

    # Also copy DICOMDIR if present (handy locally, Orthanc ignores it).
    # It too lives on the damaged disc: a CRC error here must not kill the
    # import when the images themselves are already copied.
    $dicomdir = Join-Path $srcRoot 'DICOMDIR'
    if (Test-Path $dicomdir) {
        try {
            Copy-Item -LiteralPath $dicomdir -Destination (Join-Path $dst 'DICOMDIR') -Force
        } catch {
            $unreadable.Add((L "$dicomdir  [copy failed] $($_.Exception.Message)" "$dicomdir  [copie impossible] $($_.Exception.Message)"))
        }
    }

    if ($copied -eq 0) {
        Remove-Item -Path $dst -Recurse -Force -ErrorAction SilentlyContinue
        Fail (L "No DICOM file found on $srcRoot ($scanned files scanned)." "Aucun fichier DICOM trouve sur $srcRoot ($scanned fichiers scannes).")
    }

    # Eject the CD/DVD - we are done reading from it.
    try {
        $shell = New-Object -ComObject Shell.Application
        $shell.Namespace(17).ParseName($drive.DeviceID).InvokeVerb('Eject')
        $ejectMsg = (L "CD ejected ($($drive.DeviceID))." "CD ejecte ($($drive.DeviceID)).")
    } catch {
        $ejectMsg = (L "CD ejection failed: $($_.Exception.Message)" "Ejection du CD echouee: $($_.Exception.Message)")
    }

} else {
    # ---------- RETRY MODE ----------
    # No CD inserted -> look for the most recent import folder containing a
    # non-empty _failed-files.txt, and retry the uploads.
    Update-Status -Phase (L 'No CD - looking for something to retry...' 'Pas de CD - recherche d''un retry possible...') -Max 0

    # Two leads, in this order.
    #
    # 1. A non-empty _failed-files.txt: the precise lead. The upload took place
    #    and left the list of what did not get through.
    #
    # 2. Failing that, a folder that still contains files. It is an invariant
    #    of the script: each file is deleted as soon as its upload succeeds,
    #    and the dated folder is erased when everything has gone through. A
    #    file still there was therefore NEVER sent.
    #
    # Lead 2 was missing, and it is exactly the case the CRC crash opened up:
    # the import died during the copy, hence before any upload, hence without
    # ever writing a _failed-files.txt. Five folders with 790 copied images sat
    # on the disk while the script answered "no folder contains files to retry".
    $tousDossiers = @(Get-ChildItem -Path $importBase -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending)

    # ALL pending folders, not only the most recent. The end-of-run cleanup
    # only erases folders that were actually sent: handling just one left the
    # others piling up indefinitely, to be re-run one by one. Orthanc
    # deduplicates on the SOP Instance UID, so re-sending a study already
    # present creates nothing -- it is safe, just slower.
    # Only the folders THIS script created: <date>_<time>. The end-of-run
    # cleanup deletes any folder that supplied a sent file -- without this
    # filter, a folder dropped by hand into C:\DICOM-Import (an export burnt by
    # a colleague, a copy kept aside) would be absorbed and then ERASED on the
    # first run without a CD. The images would end up in Orthanc, so nothing
    # would be lost, but nobody asked for that.
    $motifDate = '^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$'
    $enAttente = @($tousDossiers | Where-Object {
        $_.Name -match $motifDate -and (
            $(  $f = Join-Path $_.FullName '_failed-files.txt'
                ((Test-Path $f) -and ((Get-Item $f).Length -gt 0)) -or
                @(Get-ChildItem -LiteralPath $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -notlike '_*' -and $_.Name -ne 'DICOMDIR' }).Count -gt 0 )
        )
    })

    if (-not $enAttente) {
        Fail (L "No CD inserted, and no folder in $importBase contains files to send." "Aucun CD insere, et aucun dossier dans $importBase ne contient de fichiers a envoyer.")
    }

    # The most recent one serves as the landing place for _failed-files.txt and
    # the error log. The upload loop already knows how to work on paths spread
    # across several dated folders.
    $candidate = $enAttente[0]

    # "Resume" if there is at least one folder without a failure list: those
    # files were never sent, they did not fail.
    $reprisePartielle = [bool](@($enAttente | Where-Object {
        $f = Join-Path $_.FullName '_failed-files.txt'
        -not ((Test-Path $f) -and ((Get-Item $f).Length -gt 0))
    }).Count)

    $mode = 'retry'
    $dst = $candidate.FullName
    $srcRoot = (L "(resuming folder $($candidate.Name))" "(reprise du dossier $($candidate.Name))")
    $dicomFiles = New-Object System.Collections.Generic.List[string]

    Update-Status -Phase (L "Resume: listing $($enAttente.Count) folder(s)..." "Reprise : inventaire de $($enAttente.Count) dossier(s)...") -Max 0
    foreach ($dossier in $enAttente) {
        $listeEchecs = Join-Path $dossier.FullName '_failed-files.txt'

        if ((Test-Path $listeEchecs) -and ((Get-Item $listeEchecs).Length -gt 0)) {
            # Some uploads failed: stick to the list, it is precise.
            Get-Content -Path $listeEchecs -Encoding UTF8 | ForEach-Object {
                $line = $_.Trim()
                # DICOMDIR: listed by versions that took it for an image. Skipped.
                if ($line -and (Test-Path -LiteralPath $line) -and (Split-Path $line -Leaf) -ne 'DICOMDIR') { $dicomFiles.Add($line) }
            }
            # Archived for history: the new list will be written at the end.
            $arch = Join-Path $dossier.FullName (
                '_failed-files.txt.previous-' + (Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'))
            Move-Item -LiteralPath $listeEchecs -Destination $arch -Force
            continue
        }

        # Import interrupted before the upload: take everything that is there.
        # The DICM signature is re-checked rather than trusting the name: a
        # file truncated by a read error must not be sent.
        # DICOMDIR is left out, Orthanc does nothing with it.
        foreach ($f in (Get-ChildItem -LiteralPath $dossier.FullName -Recurse -File -Force -ErrorAction SilentlyContinue)) {
            if ($f.Name -like '_*' -or $f.Name -eq 'DICOMDIR' -or $f.Length -lt 132) { continue }
            $fs = $null
            try {
                $fs = [System.IO.File]::OpenRead($f.FullName)
                $fs.Seek(128, 'Begin') | Out-Null
                $buf = New-Object byte[] 4
                $null = $fs.Read($buf, 0, 4)
                if ($buf[0] -eq 0x44 -and $buf[1] -eq 0x49 -and $buf[2] -eq 0x43 -and $buf[3] -eq 0x4D) {
                    $dicomFiles.Add($f.FullName)
                }
            } catch { }
            finally { if ($fs) { $fs.Close() } }
        }
    }
    $copied = $dicomFiles.Count

    if ($copied -eq 0) {
        Fail (L "No usable DICOM file remains in $importBase." "Aucun fichier DICOM exploitable ne subsiste dans $importBase.")
    }

    # (the failure lists have already been archived folder by folder above)

    $libelle = if ($reprisePartielle) { (L 'interrupted import' 'import interrompu') } else { (L 'failed uploads' 'envois echoues') }
    $source = if ($enAttente.Count -gt 1) { (L "$($enAttente.Count) folders" "$($enAttente.Count) dossiers") } else { $candidate.Name }
    Update-Status -Phase (L "PHASE 2/2: Resume ($libelle) - $copied file(s)" "PHASE 2/2 : Reprise ($libelle) - $copied fichier(s)") -Current 0 -Max $copied -Counter "0 / $copied" -Details "Source: $source"
}

# --- Upload to Orthanc file by file
# A global ZIP is avoided so as not to exceed the Cloudflare limit (100MB per request on Free/Pro).
# Each .dcm is sent individually with a short delay to respect the nginx rate limit (2r/s).
$pair = "${orthancUser}:${orthancPwd}"
$basicAuth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
$headers = @{ Authorization = "Basic $basicAuth" }

# Cloudflare Access headers (checked by the CF edge BEFORE even reaching nginx)
if ($cfClientId -and $cfClientSecret) {
    $headers['CF-Access-Client-Id']     = $cfClientId
    $headers['CF-Access-Client-Secret'] = $cfClientSecret
}

$uploaded = 0
$failed = 0
$firstError = $null
$errorLog = Join-Path $dst '_upload-errors.log'   # detailed failure log
$failedListPath = Join-Path $dst '_failed-files.txt'  # list of failed paths (for retry)
$statusCounts = @{}   # e.g. { 403 = 12; 502 = 3 } to detect a global pattern

$total = $dicomFiles.Count

# Clear read-only on every file to upload (defence in depth, also covers the
# RETRY mode where files come from an old import that did not have the copy
# fix above). Without it, Invoke-RestMethod -InFile fails with "Access denied"
# on files inherited from a CD.
Update-Status -Phase (L 'PHASE 2/2: Preparing upload (clearing read-only)...' 'PHASE 2/2 : Preparation upload (clear read-only)...') -Max 0
foreach ($p in $dicomFiles) {
    try { (Get-Item -LiteralPath $p).IsReadOnly = $false } catch { }
}

Update-Status -Phase (L 'PHASE 2/2: Uploading to Orthanc...' 'PHASE 2/2 : Upload Orthanc...') -Current 0 -Max $total -Counter "0 / $total" -Details ''

$index = 0
foreach ($f in $dicomFiles) {
    # Let the form process button / X clicks before each iteration
    [System.Windows.Forms.Application]::DoEvents()
    # Cancellation requested through the "Stop" button or X: exit cleanly and
    # persist the files NOT YET attempted into _failed-files.txt (otherwise
    # they would be lost, since the old file has already been moved to archive).
    if ($script:cancelRequested) {
        $remaining = $dicomFiles | Select-Object -Skip $index
        foreach ($r in $remaining) {
            try { Add-Content -Path $failedListPath -Value $r -Encoding UTF8 } catch { }
        }
        Update-Status -Phase (L 'Upload stopped by the user' 'Upload interrompu par l''utilisateur') -Details ''
        break
    }
    $index++
    $retries = 0
    $maxRetries = 3
    $success = $false
    while (-not $success) {
        try {
            # /api-upload/instances, NOT /instances.
            #
            # /instances is an interface route, protected by Authelia: a
            # programmatic upload gets a 302 to the login page there.
            # /api-upload/ is the route meant for this script -- Cloudflare
            # Access guards it at the edge, and Orthanc's anonymous profile
            # allows uploading there, nothing else.
            #
            # -MaximumRedirection 0 is the safeguard, and it is ESSENTIAL.
            # Without it, Invoke-RestMethod FOLLOWS the redirect, gets the login
            # page with a 200, raises no exception -- and the script concludes
            # success and then DELETES the local file. Measured on 2026-08-30:
            # 226 consecutive uploads counted as successful, while Orthanc had
            # received none. Files from a CD would have vanished without ever
            # entering the PACS.
            Invoke-RestMethod -Uri "$orthancUrl/api-upload/instances" `
                              -Method Post `
                              -Headers $headers `
                              -InFile $f `
                              -ContentType 'application/dicom' `
                              -MaximumRedirection 0 `
                              -TimeoutSec 120 | Out-Null
            $uploaded++
            $success = $true
            # The file is safe in Orthanc -> delete the local copy. If the
            # upload is interrupted later, a re-run in RETRY mode only retries
            # the files still present (uploaded ones are deduplicated by
            # SOPInstanceUID on the Orthanc side anyway, so no risk of a
            # duplicate even with redundancy).
            try { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue } catch { }
        } catch {
            $statusCode = $null
            if ($_.Exception.Response) {
                try { $statusCode = [int]$_.Exception.Response.StatusCode } catch { }
            }
            # 429 (rate limit) or 5xx (transient server error) -> exponential backoff + retry
            $isRetryable = ($statusCode -eq 429) -or ($statusCode -ge 500 -and $statusCode -lt 600)
            if ($isRetryable -and $retries -lt $maxRetries) {
                Start-Sleep -Seconds ([Math]::Pow(2, $retries))
                $retries++
                continue
            }
            # Final failure: log + count
            $failed++
            $name = Split-Path $f -Leaf
            $errMsg = $_.Exception.Message
            $codeStr = if ($statusCode) { "HTTP $statusCode" } else { (L 'network error' 'erreur reseau') }

            # Line in the detailed log (UTF8 to handle non-ASCII file names)
            $stamp = (Get-Date).ToString('HH:mm:ss')
            Add-Content -Path $errorLog -Value "[$stamp] $codeStr - $f`r`n         -> $errMsg`r`n" -Encoding UTF8
            # List of failed paths (1 per line, simple format for retry)
            Add-Content -Path $failedListPath -Value $f -Encoding UTF8

            # First failure remembered for quick display
            if (-not $firstError) {
                $firstError = "${name} (${codeStr}): $errMsg"
            }
            # Statistics per HTTP code (detect a global problem, e.g. all 403)
            $key = if ($statusCode) { "$statusCode" } else { 'net' }
            if ($statusCounts.ContainsKey($key)) { $statusCounts[$key]++ } else { $statusCounts[$key] = 1 }

            $success = $true   # leaves the while loop (final failure)
        }
    }
    # Update UI - only counters are shown (not the file name, potential PII)
    $done = $uploaded + $failed
    $detail = if ($failed -gt 0) { (L "$uploaded succeeded, $failed failed" "$uploaded reussis, $failed echecs") } else { (L "$uploaded succeeded" "$uploaded reussis") }
    Update-Status -Current $done -Max $total -Counter "$done / $total" -Details $detail

    # Short delay to stay under the nginx rate limit (2r/s sustained)
    Start-Sleep -Milliseconds 500
}

$pushOk = ($uploaded -gt 0 -and $failed -eq 0)
$pushMsg = (L "$uploaded/$($dicomFiles.Count) DICOM file(s) uploaded to Orthanc." "$uploaded/$($dicomFiles.Count) fichier(s) DICOM uploades vers Orthanc.")

# Cleanup of the affected dated folders:
#   - If EVERYTHING succeeded (no failure, no cancellation) -> rm -rf the dated
#     folder(s) that held the uploaded files. Since each file has just been
#     deleted after its successful upload (see Remove-Item in the loop), only
#     the empty shells + DICOMDIR + possible non-DICOM files remain.
#   - If failures OR cancellation -> the folder is kept intact (remaining
#     files + _failed-files.txt + _upload-errors.log) to allow a clean re-run.
# The "combined retry" case is supported, where _failed-files.txt holds paths
# pointing to several different dated folders: the set of dated folders
# actually touched is collected.
# $unreadable.Count: the cleanup would erase _unreadable-files.txt along with
# the folder. As long as unreadable files remain, everything stays in place --
# it is the record of what is missing, and it must survive a "successful" import.
if ($pushOk -and -not $script:cancelRequested -and $unreadable.Count -eq 0) {
    $importBaseTrimmed = $importBase.TrimEnd('\','/')
    $affectedDatedFolders = $dicomFiles | ForEach-Object {
        # Walk up to the folder directly under $importBase
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
        $pushMsg += (L "`r`n$($affectedDatedFolders.Count) local folder(s) deleted after successful upload." "`r`n$($affectedDatedFolders.Count) dossier(s) local(aux) supprime(s) apres upload reussi.")
    }
}
if ($failed -gt 0) {
    # Summary of HTTP codes to spot a systemic problem (e.g. all 403 = CF auth problem)
    $codesSummary = ($statusCounts.GetEnumerator() | ForEach-Object { "$($_.Value)x $($_.Key)" }) -join ', '
    $pushMsg += (L "`r`n$failed failure(s) [$codesSummary]" "`r`n$failed echec(s) [$codesSummary]")
    $pushMsg += (L "`r`nFirst: $firstError" "`r`nPremier: $firstError")
    $pushMsg += (L "`r`nDetailed log: $errorLog" "`r`nLog detaille: $errorLog")
    $pushMsg += (L "`r`nList to replay: $failedListPath" "`r`nListe a rejouer: $failedListPath")
}

# --- Summary
Close-Status   # close the progress window before showing the final popup

$lines = New-Object System.Collections.Generic.List[string]
if ($mode -eq 'import') {
    $lines.Add((L "Mode: CD import" "Mode: Import CD"))
    $lines.Add("Source: $srcRoot")
    $lines.Add("Destination: $dst")
    $lines.Add((L "$copied DICOM file(s) copied (out of $scanned scanned)." "$copied fichier(s) DICOM copie(s) (sur $scanned scannes)."))
    if ($ejectMsg) { $lines.Add($ejectMsg) }
} elseif ($reprisePartielle) {
    # A useful distinction: "retry" suggests uploads had failed, whereas here
    # the previous import had stopped before even attempting one.
    $lines.Add((L "Mode: Resuming an interrupted import" "Mode: Reprise d'un import interrompu"))
    $lines.Add((L "Folder(s): $($enAttente.Count) pending in $importBase" "Dossier(s): $($enAttente.Count) en attente dans $importBase"))
    $lines.Add((L "$copied file(s) copied but never sent." "$copied fichier(s) copie(s) mais jamais envoye(s)."))
} else {
    $lines.Add((L "Mode: Retrying failed uploads" "Mode: Nouvelle tentative sur les envois echoues"))
    $lines.Add((L "Folder(s): $($enAttente.Count) pending in $importBase" "Dossier(s): $($enAttente.Count) en attente dans $importBase"))
    $lines.Add((L "$copied file(s) to send again." "$copied fichier(s) a renvoyer."))
}
if ($unreadable.Count -gt 0) {
    # The fact, with no advice or instruction: the operator knows what to do
    # with a damaged CD, and the import happens between two patients.
    $lines.Add((L "$($unreadable.Count) unreadable file(s) on the disc, not imported." "$($unreadable.Count) fichier(s) illisible(s) sur le disque, non importe(s)."))
    $lines.Add((L "List: $(Join-Path $dst '_unreadable-files.txt')" "Liste : $(Join-Path $dst '_unreadable-files.txt')"))
}
$lines.Add($pushMsg)
$summary = $lines -join "`r`n"

Show-Box -Message $summary -Icon $(if ($pushOk -and $unreadable.Count -eq 0) { 'Information' } else { 'Warning' })
