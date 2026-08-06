# ORTHANC-AUTHELIA

Medical PACS solution based on Orthanc with Authelia authentication (SSO, 2FA, RBAC), OHIF viewer, and custom token management system.

**Platform Support**: x86-64 Linux only

## Overview

ORTHANC-AUTHELIA is a complete Picture Archiving and Communication System (PACS) for small to medium healthcare structures. It combines:
- **Orthanc PACS** - Industry-standard DICOM server with PostgreSQL storage
- **Authelia** - Modern authentication with SSO and 2FA
- **OHIF Viewer v3.11.0** - Professional medical imaging viewer
- **Custom Auth-Service** - Token-based external sharing with OE2-themed management UI
- **Multiple Viewers** - OHIF, Stone Web Viewer, and VolView for different use cases

## Stack & Versions

Component versions as defined in `docker-compose.yml` (keep this table in sync when bumping images):

| Component | Image | Version |
|-----------|-------|---------|
| Orthanc PACS | `orthancteam/orthanc` | `26.6.1` |
| Authelia | `authelia/authelia` | `4.39.20` |
| Redis | `redis` | `8.0-alpine` |
| OHIF Viewer | `registry.yokoinc.ovh/orthanc-ohif` | `3.11.0` |
| Nginx | `registry.yokoinc.ovh/orthanc-nginx` | `1.1.0` |
| Auth-Service | `registry.yokoinc.ovh/orthanc-auth-service` | `1.0.15` |
| Socket proxy | `tecnativa/docker-socket-proxy` | `0.3.0` |

> **PostgreSQL** is not part of this stack — Orthanc connects to an **external** PostgreSQL instance over the `database` network (see [Database Setup Guide](docs/DATABASE_SETUP.md)).

## Why Authelia over KeyCloak?

- **Lightweight**: Minimal resource usage vs KeyCloak's heavy footprint
- **Simple Configuration**: File-based config vs complex realm management
- **Docker Native**: Built for containerized environments
- **Healthcare Focus**: Perfect for medical environments with simpler needs

## Architecture

```
                  ORTHANC-AUTHELIA - Dual Access Flow
                  ===================================

                             [ BROWSER ]
         Auth Access              │                Shares
        ┌─────────────────────────┴──────────────────────┐
        ▼                                                ▼
 https://pacs/...                          https://pacs/share/?token=xxxxx
        │                                                │
  ┌─────┴──────┐                                   ┌─────┴──────┐
  │ NGINX      │                                   │ NGINX      │
  └─────┬──────┘                                   └─────┬──────┘
        │ auth_request                                   │ direct
        ▼                                                ▼
┌───────────────────┐                          ┌────────────────────┐
│ Authelia          │ ----────> REDIS <─────── │ Auth-Service       │
│ (SSO + 2FA)       │                          │ (token validation) │
└───────────────────┘                          └────────────────────┘
        │                                                │
        ▼                                                ▼
 ┌──────────────┐                              ┌────────────────────┐
 │ Orthanc      │ <─── Authorization Plugin ───┤ • OHIF Viewer      │
 │ + OHIF       │                              │ • Limited access   │
 └──────────────┘                              │ • Token expiry     │
                                               └────────────────────┘
```

**Authentication Flow**:
1. **User Login**: Browser → Authelia → Session cookie → Full access
2. **Token Sharing**: Share link → Auth-Service → Limited study access

## Key Features

- **Dual Authentication**: Authelia for users + token system for external sharing
- **Role-Based Access**: Admin, Doctor, External user roles with granular permissions
- **Three Medical Viewers**: OHIF (primary), Stone Web Viewer (advanced), VolView (3D)
- **Secure Sharing**: Time-limited, usage-limited tokens with copy-to-clipboard links
- **Token Manager**: OE2-themed admin dashboard with patient name resolution from DICOM metadata
- **OE2 Sidebar Integration**: "Partages" button injected directly into Orthanc Explorer 2
- **Programmatic Upload Endpoint**: Optional `/api-upload/instances` route for automated DICOM ingestion (scripts, batch imports) — bypasses Authelia SSO, uses HTTP Basic auth + dedicated `uploader` role restricted to POST-only
- **PostgreSQL Storage**: High-performance database backend
- **SSL Auto-Generation**: Self-signed certificates or custom SSL
- **Easy User Management**: Interactive scripts for user administration

## Quick Start

### Prerequisites

- Docker Engine 20.10+ with Compose plugin v2
- 4GB RAM minimum (8GB recommended)
- openssl (for secret generation, ships with most distros)

### Installation (3 commands)

```bash
git clone https://github.com/yokoinc/orthanc-authelia.git
cd orthanc-authelia
./bootstrap.sh && docker compose up -d
```

`bootstrap.sh` s'occupe de tout : génération des secrets aléatoires (Authelia ×3, PostgreSQL, compte de service Orthanc), copie des templates de configuration, substitution des variables dans la config Authelia, et génération d'un hash argon2id valide. Aucune valeur à saisir à la main.

À partir de là :

1. **Assistant d'installation** — https://localhost:30443/console/setup
   Créer le compte administrateur. Accessible sans authentification uniquement au premier démarrage ; la porte se ferme dès que l'assistant est finalisé.
2. **Orthanc Explorer** — https://localhost:30443/
   Connexion avec le compte qui vient d'être créé.
3. **Panel d'administration** — https://localhost:30443/console/
   Gestion des utilisateurs, configuration Orthanc, état des composants. Réservé au groupe `admin`.

Le certificat TLS est auto-généré et auto-signé : accepter l'avertissement du navigateur au premier accès.

> **Certificat auto-signé et HSTS.** En `SSL_MODE=selfsigned`, la pile n'envoie
> volontairement pas de HSTS (`max-age=0`). Les deux sont incompatibles : une
> fois la directive enregistrée, les navigateurs refusent d'afficher l'exception
> de certificat et le site devient inaccessible, page vide sans explication. Si
> tu as visité le site à une époque où le HSTS était encore émis, purge-le une
> fois via `chrome://net-internals/#hsts`, section « Delete domain security
> policies ».

### Interface d'administration

Le panel est une application Vue 3 (`services/auth-service/frontend/`) compilée par Vite et embarquée dans l'image auth-service au build. Six onglets :

| Onglet | Ce qu'il fait | Mécanisme |
|---|---|---|
| **Utilisateurs** | Créer, modifier, désactiver et supprimer les comptes ; changer un mot de passe | Écrit `users_database.yml`, relu à chaud par Authelia en quelques secondes |
| **Configuration Orthanc** | ~40 paramètres de `orthanc.json`, rangés par thème et documentés, et un bouton pour redémarrer Orthanc | Écrit le JSON ; le redémarrage applique les changements |
| **Équipements** | Déclarer les modalités DICOM (scanner, IRM…) et tester leur connectivité par C-ECHO | Passe par l'API d'Orthanc ; effet immédiat, sans redémarrage |
| **État** | Redis, Orthanc, fichiers de configuration | Lecture seule |
| **Maintenance** | Adresse publique de la pile, viewer par défaut des liens de partage, sauvegardes | Écrit `.env` ; restaure un fichier depuis `data/admin-backups/` |
| **Journal** | Qui a fait quoi et quand : comptes, configuration, sauvegardes, requêtes rejetées | Lit le flux d'audit dans Redis |

Chaque écriture crée une sauvegarde rotative dans `data/admin-backups/` (les 10 dernières sont conservées). Le panel **édite** `orthanc.json` au lieu de le régénérer : seule la valeur visée est remplacée, et les commentaires qui documentent chaque réglage survivent. Une régénération par `json.dumps()` les effaçait — constaté sur une installation réelle, où la première modification faite depuis le panel avait supprimé les 44 commentaires du fichier. Le résultat est relu et comparé à la structure attendue avant d'être écrit ; si la mise en forme ne peut pas être conservée, la réponse de l'API le dit au lieu de laisser le découvrir plus tard. La gestion des comptes ne demande aucun accès au socket Docker : Authelia surveille son fichier d'utilisateurs et le relit seul.

#### Redémarrer Orthanc depuis le panel

La configuration d'Orthanc, elle, ne se recharge pas à chaud. L'image `orthancteam` **génère** `/tmp/orthanc.json` au démarrage, en fusionnant ses valeurs par défaut, les fichiers de `/etc/orthanc/` et les variables `ORTHANC__*` ; c'est ce fichier que le processus lit. `POST /tools/reset` ne relit que le fichier généré, donc ne voit aucune modification de notre `orthanc.json`, qui n'en est que la source. Seul un redémarrage du conteneur, qui le régénère, applique un changement.

À distance, un accès SSH n'est pas toujours disponible : sans issue depuis le panel, modifier la configuration y laisse l'exploitant bloqué. Le bouton **Redémarrer Orthanc** passe donc par un service dédié, `socket-proxy`, qui n'est joignable que depuis le réseau interne (aucun port publié). Le socket Docker n'est **pas** monté dans auth-service : le donner à un service exposé au web reviendrait à lui accorder l'équivalent de root sur l'hôte.

Le périmètre du proxy est volontairement minuscule — `POST=1`, `ALLOW_RESTARTS=1`, et **tout le reste à 0**, `CONTAINERS` compris. Ce dernier point n'est pas cosmétique : avec `CONTAINERS=1`, `POST=1` ouvre l'intégralité de `/containers/*`, y compris `POST /containers/create`. Vérifié pendant la mise au point, un conteneur privilégié montant la racine de l'hôte était alors accepté (HTTP 201) — soit exactement l'évasion que ce montage doit empêcher. Avec `CONTAINERS=0`, `create` et `exec` répondent 403 et le redémarrage fonctionne toujours. `scripts/e2e-test.sh` rejoue ces trois vérifications, pour qu'un retour en arrière d'une ligne dans le compose ne passe pas inaperçu.

La route attend qu'Orthanc réponde à nouveau avant de rendre la main, plutôt que de conclure dès que Docker a rendu la main : une configuration acceptée à l'écriture peut très bien empêcher Orthanc de redémarrer, et cela doit se voir tout de suite. Passé 60 secondes sans réponse, le panel renvoie vers les journaux du conteneur et la restauration d'une sauvegarde. Demande et résultat sont tracés dans le journal d'audit (`orthanc.restart.requested`, puis `orthanc.restarted` ou l'échec correspondant).

Pour désactiver la fonction, laisser `DOCKER_PROXY_URL` vide dans `.env` et retirer le service `socket-proxy` du compose : le panel indique alors la commande à lancer à la main.

Deux garde-fous méritent d'être connus. On ne peut ni supprimer, ni désactiver, ni retirer du groupe `admin` le dernier administrateur actif : la pile se retrouverait sans personne pour l'administrer, et la seule issue serait d'éditer `users_database.yml` à la main. Et l'assistant de première installation se ferme définitivement une fois finalisé — il ne peut pas servir à créer un second administrateur.

Le même onglet fixe le viewer proposé par défaut lorsqu'on partage un examen depuis Explorer — OHIF, Stone Web Viewer ou VolView (`SHARE_DEFAULT_VIEWER`). Contrairement à l'URL publique, ce réglage prend effet immédiatement : la valeur est relue dans `.env` à chaque appel, et Explorer redemande ces réglages chaque fois qu'on ouvre le menu de partage. Une valeur inconnue est ignorée au profit d'OHIF plutôt que de casser le menu. Le choix reste modifiable au cas par cas au moment du partage.

L'onglet Maintenance permet aussi de changer l'URL publique (`PUBLIC_URL`), ce qui met à jour `.env` et la configuration Authelia d'un seul geste. Le changement prend effet au redémarrage de la pile, et impose de se reconnecter sur la nouvelle adresse — le cookie de session étant lié à l'ancien domaine.

Pour développer le frontend avec hot-reload :

```bash
cd services/auth-service/frontend
npm install
npm run dev    # proxifie /api vers localhost:8000
```

### Réinitialiser complètement

```bash
docker compose down -v      # stop + supprime volumes (data included)
rm -rf services/authelia/config/{configuration.yml,users_database.yml}
rm -rf services/orthanc/config/orthanc.json .env docker-compose.yml
./bootstrap.sh              # regénère tout à neuf
docker compose up -d
```

### Advanced : DB PostgreSQL externe

Si tu as déjà un postgres tournant ailleurs, crée un `docker-compose.override.yml` à la racine :

```yaml
services:
  postgres:
    profiles: ["disabled"]    # ne démarre jamais
  orthanc:
    networks:
      - orthanc-network
      - database              # ton réseau externe existant
    environment:
      - ORTHANC__POSTGRESQL__HOST=database
networks:
  database:
    external: true
```

Voir [Database Setup Guide](docs/DATABASE_SETUP.md) pour plus de détails.

## Access Points

Default ports: `30080` (HTTP) and `30443` (HTTPS)

- **Main Interface**: `https://your-domain/` (requires authentication)
- **OHIF Viewer**: `https://your-domain/ohif/` (primary medical viewer)
- **Orthanc Explorer 2**: `https://your-domain/ui/` (PACS administration)
- **Stone Web Viewer**: `https://your-domain/stone-webviewer/` (advanced viewer)
- **VolView**: `https://your-domain/volview/` (3D volumetric viewer)
- **Panel d'administration**: `https://your-domain/console/` (groupe `admin`)
- **Token Management**: `https://your-domain/auth/tokens/manage` (admin only)
- **External Shares**: `https://your-domain/share/?token=xxx` (no auth required)
- **Programmatic Upload**: `POST https://your-domain/api-upload/instances` (HTTP Basic auth, see [Programmatic Upload Endpoint](#programmatic-upload-endpoint))

## Configuration

### Essential Files

| File | Purpose | Example |
|------|---------|---------|
| `.env` | Environment variables | `.env.example` |
| `docker-compose.yml` | Service orchestration | `docker-compose.yml.example` |
| `services/authelia/config/configuration.yml` | Authelia config | `authelia-configuration.yml.example` |
| `services/authelia/config/users_database.yml` | User accounts | `authelia-users.yml.example` |
| `services/orthanc/config/orthanc.json` | Orthanc PACS config | `orthanc.json.example` |

### Critical: Credential Synchronization

These credentials **must match** across files:
- `.env`: `AUTH_USERNAME` / `AUTH_PASSWORD`
- `orthanc.json`: `WebServiceUsername` / `WebServicePassword`

### SSL Configuration

Three modes available via `SSL_MODE` in `.env`:

- **`selfsigned`** (default): Auto-generated certificates, perfect for development
- **`disabled`**: HTTP only, use when behind reverse proxy
- **`custom`**: Your own certificates (Let's Encrypt, commercial CA)

See [SSL Setup Guide](docs/SSL_SETUP.md) for detailed configuration.

## User Management

Create or modify users:
```bash
./manage-authelia-users.sh
```

Available user groups:
- **`admin`**: Full access including token management
- **`doctor`**: Medical data access (OHIF, Orthanc Explorer)
- **`external`**: Limited read-only access
- **`user`**: Basic authenticated access

After modifying users:
```bash
docker-compose restart authelia
```

## Programmatic Upload Endpoint

For automated DICOM ingestion (CD/DVD import scripts, batch jobs, modality integration), the stack exposes an optional `POST /api-upload/instances` route that **bypasses Authelia SSO** and uses HTTP Basic authentication instead. This is necessary because Authelia is designed for interactive browser logins and cannot be authenticated programmatically.

### Threat model and defense in depth

This endpoint is intentionally restricted to **upload only**:

- **Path scope**: only `/api-upload/instances` is exposed — no other Orthanc API
- **Auth chain**: HTTP Basic (nginx `htpasswd`) + dedicated `uploader` role (Orthanc Authorization plugin)
- **Role restriction**: the `uploader` role can ONLY `POST /instances`. Any other operation (read, list, delete, modify, share, system access) is denied by the auth-service
- **Rate limiting**: 2 requests/second sustained, burst of 5
- **Body size**: capped at 4 GB (large enough for ZIP archives of full DICOM CDs)

**If the htpasswd credentials are ever compromised**, the worst an attacker can do is fill the storage with bogus DICOM files (DoS by disk fill). No existing patient data can be read, listed, exfiltrated, or deleted through this endpoint.

For additional protection, the endpoint can be placed behind a Cloudflare Access Service Token, IP allowlist, or mTLS at the reverse-proxy layer.

### Enabling the endpoint

Set both variables in `.env` (leave empty to disable — nginx will return 500 on `/api-upload/*` requests, fail-closed):

```bash
UPLOAD_USER=upload-service
UPLOAD_PASSWORD=$(openssl rand -base64 24)
```

The nginx entrypoint generates `/etc/nginx/htpasswd` from these values at container start (SHA-256 hashed via `openssl passwd -5`). No manual file management required.

### Client usage

Single DICOM file:
```bash
curl -u "$UPLOAD_USER:$UPLOAD_PASSWORD" \
     --data-binary @image.dcm \
     -H "Content-Type: application/dicom" \
     https://your-domain/api-upload/instances
```

ZIP archive containing multiple DICOM files (Orthanc auto-detects the archive):
```bash
curl -u "$UPLOAD_USER:$UPLOAD_PASSWORD" \
     --data-binary @study.zip \
     -H "Content-Type: application/zip" \
     https://your-domain/api-upload/instances
```

Les identifiants sont ceux générés par `bootstrap.sh` dans `.env` (`UPLOAD_USER` / `UPLOAD_PASSWORD`). L'endpoint ne demande pas de session Authelia — c'est la voie des scripts — mais il refuse les requêtes non authentifiées : il accepte des données médicales, le laisser ouvert permettrait à quiconque sur le réseau d'alimenter la base.

Note: if your reverse proxy or CDN enforces a body size limit smaller than your typical DICOM payload (e.g. Cloudflare Free/Pro caps at 100 MB), upload files individually rather than as a single archive.

### Internals

Request flow for `POST /api-upload/instances`:

1. **nginx** receives the request, matches `location ~ ^/api-upload/(instances)(?:/|$)`
2. **nginx Basic auth** validates `Authorization: Basic ...` against `/etc/nginx/htpasswd` (no Authelia call)
3. **nginx rewrites** the URL: `/api-upload/instances` → `/instances`
4. **nginx injects** `Remote-User: uploader` (and `X-Auth-User: uploader`, `Remote-Groups: uploader`)
5. **Orthanc Authorization plugin** reads `Remote-User` and POSTs to `auth-service:/tokens/validate`
6. **auth-service** maps `uploader` → `uploader-role` and grants the request **only if** `method == "post"` and `level == "instance"`
7. **Orthanc** ingests the DICOM into its standard storage pipeline (PostgreSQL + filesystem)

## Docker Registry

Trois images sont publiées sur `registry.yokoinc.ovh` :

- `orthanc-nginx:1.1.0` — nginx, génération du certificat, authentification déléguée
- `orthanc-ohif:3.11.0` — visionneuse OHIF servie sous `/ohif/`
- `orthanc-auth-service:1.0.16` — service d'authentification, panel et partages

**Aucun accès à ce registre n'est nécessaire.** Chaque service déclare à la fois
`image:` et `build:` : l'image publiée est utilisée si elle est disponible, et
construite depuis le dépôt sinon. Un clone frais démarre donc sans rien tirer.

Pour forcer la construction locale :

```bash
docker compose build                 # les trois
docker compose build nginx           # un seul
```

Le build d'OHIF prend une quinzaine de minutes la première fois : la visionneuse
est compilée depuis ses sources. C'est indispensable — OHIF fige son chemin de
base dans le bundle au moment du build, et l'image officielle `ohif/app`, prévue
pour être servie à la racine, ne fonctionne pas sous `/ohif/`.

Pour publier sur ton propre registre, remplacer les `image:` du
`docker-compose.yml` puis `docker compose build && docker compose push`.

## Documentation

Detailed guides available in `docs/`:

- **[Configuration Guide](docs/CONFIGURATION.md)** - Complete variable reference
- **[SSL Setup Guide](docs/SSL_SETUP.md)** - All SSL modes and reverse proxy setup
- **[Database Setup Guide](docs/DATABASE_SETUP.md)** - PostgreSQL configuration
- **[Troubleshooting Guide](docs/TROUBLESHOOTING.md)** - Common issues and solutions
- **[Authelia User Management](docs/AUTHELIA_USER_MANAGEMENT.md)** - User permissions
- **[Token Sharing Guide](docs/TOKEN_SHARING.md)** - External sharing workflow
- **[Auth-Service Overview](docs/AUTH_SERVICE.md)** - Authentication service details
- **[Nginx Configuration](docs/NGINX_CONFIGURATION.md)** - Reverse proxy details
- **[Cloudflare Tunnel](docs/CLOUDFLARE_TUNNEL.md)** - Exposition sans ouvrir de port

## Troubleshooting

### Quick Checks

```bash
# Etat des services
docker compose ps

# Journaux
docker compose logs -f

# Redemarrage
docker compose restart

# Certificats
docker exec orthanc-nginx ls -la /etc/nginx/ssl/
```

Pour valider une installation complète sans toucher à celle qui tourne :

```bash
./scripts/e2e-test.sh
```

### Common Issues

- **Page blanche, le site ne répond plus** : le plus souvent un HSTS enregistré
  alors que le certificat est auto-signé — le navigateur refuse alors toute
  exception. Purger via `chrome://net-internals/#hsts`. Vérifier d'abord côté
  serveur avec `curl -k https://votre-domaine/auth/` : s'il répond 200, le
  problème est bien dans le navigateur.
- **Can't login**: Run `./manage-authelia-users.sh` and restart Authelia
- **Database connection failed**: Verify PostgreSQL is on `database` network
- **Port conflicts**: Change ports in `docker-compose.yml`
- **SSL warnings**: Normal for self-signed certificates

See [Troubleshooting Guide](docs/TROUBLESHOOTING.md) for complete solutions.

## Enabled Orthanc Plugins

- **PostgreSQL**: High-performance storage/index
- **DICOMweb**: Modern web DICOM protocol
- **Authorization**: Custom permission validation
- **Explorer 2**: Modern web interface
- **Stone Web Viewer**: High-performance viewer
- **VolView**: 3D volumetric visualization
- **Housekeeper**: Automatic maintenance
- **GDCM**: Enhanced DICOM codec support

## Sources and Acknowledgments

Built upon excellent open-source projects:

- **Orthanc PACS** - Sébastien Jodogne, UCLouvain - [orthanc-server.com](https://orthanc-server.com)
- **Authelia** - Modern authentication server
- **OHIF Viewer** - Open Health Imaging Foundation
- **PostgreSQL** - High-performance database
- **Redis** - In-memory data store

Forked and enhanced by **Grégory Cuffel** for the open-source medical imaging community.
