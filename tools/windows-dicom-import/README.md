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

Un CD rayé ou sale rend certains secteurs illisibles. Le script ne s'arrête
pas dessus : il tente une relecture, note le fichier dans
`_unreadable-files.txt`, et poursuit. À la fin de la copie il annonce le
nombre de fichiers perdus et demande s'il faut envoyer l'étude quand même,
puisqu'elle sera incomplète.

Face à ce message : nettoyer le disque (chiffon doux, du centre vers le bord)
et relancer. Une relecture aboutit souvent au second passage.

Pendant une relecture la fenêtre peut se figer une à deux minutes : c'est le
pilote Windows qui insiste sur le secteur, pas un blocage du script.

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
