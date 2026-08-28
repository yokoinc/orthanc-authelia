# Poste d'import DICOM (Windows)

Importe les CD/DVD de patients vers Orthanc depuis un poste Windows.
Lancement par double-clic sur `import-dicom.bat`.

Le script choisit son mode tout seul :

- **Import** — un disque est inséré : il copie les fichiers DICOM dans
  `C:\DICOM-Import\<date>_<heure>\`, éjecte le disque, puis envoie chaque
  fichier à Orthanc un par un (la limite Cloudflare est de 100 Mo par requête).
- **Reprise** — aucun disque inséré : il reprend le dossier le plus récent
  contenant un `_failed-files.txt` et retente les envois qui avaient échoué.

## Disques abîmés

Un CD rayé ou sale rend certains secteurs illisibles. Le script ne s'arrête pas
dessus et ne pose aucune question : le fichier est noté dans
`_unreadable-files.txt`, le compteur de la fenêtre l'affiche, et la copie
continue. Le décompte figure dans le résumé final.

Aucune relecture n'est tentée : face à un secteur abîmé, le pilote Windows
insiste déjà 30 s à 2 min tout seul avant de rendre la main. Pendant ce temps
la fenêtre paraît figée -- c'est le pilote, pas le script.

## Configuration

`config.json` — non versionné, à créer depuis `config.json.example` :

```json
{
  "localFolder": "C:\DICOM-Import",
  "orthancUrl": "https://pacs.example.org",
  "orthancUser": "compte-de-depot"
}
```

Les secrets (mot de passe du dépôt, jeton de service Cloudflare Access) ne se
mettent **pas** là : lancer `setup-secrets.ps1`, qui les chiffre par DPAPI dans
`config.secrets.dpapi.json`. Ce chiffrement est lié à la machine et au compte
Windows — le fichier est inutilisable ailleurs, et n'est pas versionné non plus.
`verify-secrets.ps1` contrôle qu'ils se déchiffrent encore.

Le script accepte toujours les champs `orthancPassword`, `cfAccessClientId` et
`cfAccessClientSecret` en clair dans `config.json`, par rétro-compatibilité.
C'est à éviter : ils y restent lisibles par n'importe quel programme du poste.
