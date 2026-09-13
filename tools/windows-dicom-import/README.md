# DICOM import workstation (Windows)

Imports patient CDs/DVDs into Orthanc from a Windows workstation.
Started by double-clicking `import-dicom.bat`.

The script picks its mode by itself:

- **Import** — a disc is inserted: it copies the DICOM files into
  `C:\DICOM-Import\<date>_<time>\`, ejects the disc, then sends each file to
  Orthanc one at a time (Cloudflare's limit is 100 MB per request).
- **Resume** — no disc inserted: it picks up the pending folders (a
  `_failed-files.txt`, or files never sent) and retries the uploads.

The window and messages follow the Windows display language: French on a
French Windows, English otherwise.

## Damaged discs

A scratched or dirty CD makes some sectors unreadable. The script does not stop
on them and asks no question: the file is recorded in `_unreadable-files.txt`,
the window's counter shows it, and the copy carries on. The count appears in
the final summary.

No re-read is attempted: faced with a damaged sector, the Windows driver
already insists on its own for 30 s to 2 min before giving control back.
Meanwhile the window looks frozen -- that is the driver, not the script.

## Configuration

`config.json` — not versioned, to be created from `config.json.example`:

```json
{
  "localFolder": "C:\DICOM-Import",
  "orthancUrl": "https://pacs.example.org",
  "orthancUser": "upload-account"
}
```

The secrets (upload account password, Cloudflare Access service token) do
**not** go there: run `setup-secrets.ps1`, which encrypts them with DPAPI into
`config.secrets.dpapi.json`. That encryption is bound to the machine and the
Windows account — the file is useless anywhere else, and is not versioned
either. `verify-secrets.ps1` checks that they still decrypt.

The script still accepts the `orthancPassword`, `cfAccessClientId` and
`cfAccessClientSecret` fields in clear in `config.json`, for backward
compatibility. Avoid it: they stay readable there by any program on the
workstation.
