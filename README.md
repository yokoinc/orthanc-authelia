# ORTHANC-AUTHELIA

A complete PACS for small and medium healthcare structures: Orthanc for the
DICOM store, Authelia for single sign-on, OHIF for viewing, and a purpose-built
auth-service that adds an **administration panel** and **time-limited sharing
links** for people outside the organisation.

**Platform**: x86-64 Linux only.

## The idea

Everything is administered **from the browser**. You are never expected to open
Authelia's YAML, hand-edit a user database, or restart a container to make an
account exist.

Concretely, once the stack is up:

| You want to | You do |
|---|---|
| Create, rename, disable or delete a user | Admin panel, *Users* tab |
| Change someone's password | Admin panel, *Users* tab |
| Change an Orthanc setting | Admin panel, *Orthanc* tab |
| Declare a DICOM modality | Admin panel, *Modalities* tab |
| Change the public address | Admin panel, *Session* tab |
| Take or restore a backup | Admin panel, *Backups* tab |
| See who did what | Admin panel, *Audit* tab |
| Check that everything is alive | Admin panel, *Health* tab |

Authelia reloads its user database from disk within a second (`watch: true`),
so a user created in the panel can log in immediately. **No restart.**

Shell scripts still exist, but only as rescue paths for when the panel itself
is unreachable — see [Rescue paths](#rescue-paths).

## Stack

| Component | Image | Version |
|-----------|-------|---------|
| Orthanc PACS | `orthancteam/orthanc` | `26.6.1` |
| Authelia | `authelia/authelia` | `4.39.20` |
| Redis | `redis` | `8.0-alpine` |
| Docker socket proxy | `tecnativa/docker-socket-proxy` | `0.1.2` |
| OHIF Viewer | `registry.yokoinc.ovh/orthanc-ohif` | `3.13.4-2` |
| Nginx | `registry.yokoinc.ovh/orthanc-nginx` | `1.1.2` |
| Auth-Service | `registry.yokoinc.ovh/orthanc-auth-service` | `1.1.0` |

These are the versions pinned in `docker-compose.yml.example`. Keep this table
and that file in sync when bumping an image.

> **PostgreSQL is embedded** (`postgres:16-alpine`), and the DICOM images
> themselves are stored in it: the `orthanc_postgres_data` volume *is* the
> PACS. An installation that already runs its own PostgreSQL can use it instead
> — see the [Database Setup Guide](docs/DATABASE_SETUP.md).

> **No second factor.** Every `access_control` rule is `one_factor`, and no TOTP
> or WebAuthn credential is registered. The password is the only barrier, which
> is why the zxcvbn policy and the tightened brute-force regulation matter. See
> [docs/EXPLOITATION.md](docs/EXPLOITATION.md).

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
│ (SSO, 1 factor)   │                          │ (token validation) │
└───────────────────┘                          └────────────────────┘
        │                                                │
        ▼                                                ▼
 ┌──────────────┐                              ┌────────────────────┐
 │ Orthanc      │ <─── Authorization Plugin ───┤ • OHIF Viewer      │
 │ + OHIF       │                              │ • Limited access   │
 └──────────────┘                              │ • Token expiry     │
                                               └────────────────────┘
```

Two ways in, and only two:

1. **A session.** Browser → Authelia → session cookie → access decided by the
   user's group.
2. **A share link.** `?token=…` → auth-service validates it → access limited to
   one study, with an expiry date and a usage quota. No account needed.

## Quick start

### Prerequisites

- Docker Engine 20.10+ and Docker Compose 2.0+
- `bash` and `openssl` — on **Windows**, Docker Desktop plus Git for Windows,
  and run everything from **Git Bash**
- 4 GB RAM minimum, 8 GB recommended

Nothing else: the database, the secrets and the certificate are all created
for you.

### Three steps

```bash
git clone https://github.com/yokoinc/orthanc-authelia.git
cd orthanc-authelia
./bootstrap.sh
```

`bootstrap.sh` asks one question — the public address, press Enter to keep the
local default `https://pacs.localhost:30443` — then generates every secret
(PostgreSQL included), writes `.env`, `docker-compose.yml` and the Authelia and
Orthanc configurations, creates the directories the panel writes to, and sets
the file permissions on everything holding a secret. It refuses to overwrite an
existing installation unless given `--force`.

```bash
docker compose up -d
```

The first start takes a few minutes: images are pulled and PostgreSQL
initialises its volume. Then open the setup wizard, which creates the first
administrator:

```
https://pacs.localhost:30443/auth/setup
```

Use the address `bootstrap.sh` printed — by default `pacs.localhost`, not
`localhost`: the login session is bound to that name. The certificate is
self-signed at this point, so accept the browser warning.
The wizard closes itself permanently once an administrator exists — it cannot
be used to create a second one.

That is the whole installation. Review `.env` afterwards if you need to change
the domain, language or timezone.

### Starting over

`down -v` deletes the volumes, **the PostgreSQL one included — every stored
image goes with it**. Only for a test installation.

```bash
docker compose down -v
rm -rf .env docker-compose.yml data/admin-backups data/app-settings \
       services/authelia/config/{configuration.yml,users_database.yml} \
       services/orthanc/config/orthanc.json
./bootstrap.sh
```

## Roles

Assigned per user in the panel. One group per account.

| Group | Can view | Can upload | Can share | Admin panel |
|---|---|---|---|---|
| `admin` | yes | yes | yes | yes |
| `doctor` | yes | yes | yes | no |
| `external` | yes | no | no | no |

`admin` additionally reaches the token manager and the setup surfaces; those
routes are denied to everyone else by Authelia itself, not merely hidden.

## Access points

Default ports `30080` (HTTP) and `30443` (HTTPS).

| Address | What |
|---|---|
| `/` | Orthanc Explorer 2 — the main interface |
| `/ohif/` | OHIF viewer, the primary one |
| `/stone-webviewer/` | Stone Web Viewer |
| `/volview/` | VolView, 3D volumetric |
| `/auth/admin` | Administration panel (admin only) |
| `/auth/tokens/manage` | Share manager (admin only) |
| `/share/?token=…` | A share link — no account needed |
| `/api-upload/instances` | Programmatic upload (see below) |

## Programmatic upload

An optional route for scripts and batch imports, disabled by default. It
bypasses Authelia — a script cannot complete an interactive login — and is
protected instead by HTTP Basic auth with a dedicated `uploader` account
restricted to `POST`.

**Post to `/api-upload/instances`, never to `/instances`.** The latter is the
interface route: it sits behind Authelia, answers a programmatic POST with a
302 to the login page, and any client that follows redirects will read the
resulting 200 as a successful upload. A client that then deletes its local file
on success destroys data that never arrived. This is not hypothetical — it
happened here, and cost 186 files. Send `-MaximumRedirection 0` or the
equivalent, and treat any 3xx as a failure.

See [docs/EXPLOITATION.md](docs/EXPLOITATION.md) for the threat model, and
`tools/windows-dicom-import/` for a working client.

## Building the images

The three custom images are built from this repository:

```bash
# Auth-service
docker build -t your-registry/orthanc-auth-service:VERSION services/auth-service/sources

# Nginx
docker build -t your-registry/orthanc-nginx:VERSION services/nginx

# OHIF — long build, around 15 minutes
docker build -t your-registry/orthanc-ohif:VERSION services/ohif/docker
```

Then point `docker-compose.yml` at your own registry.

## Tests

```bash
docker build -t auth-service-test services/auth-service/sources
docker run --rm --entrypoint sh \
  -e AUTH_USERNAME=ci -e AUTH_PASSWORD=ci-password-1234 \
  -e ORTHANC_ADMIN_USER=ci -e ORTHANC_ADMIN_PASS=ci \
  -v "$PWD:/repo" -w /repo/services/auth-service/sources \
  auth-service-test \
  -c 'pip install -q -r requirements-dev.txt && python -m pytest tests/ -q'
```

The same suite, the image builds, and six repository consistency checks run on
every push — see `.github/workflows/tests.yml`.

## Documentation

- **[Operations](docs/EXPLOITATION.md)** — running it: backups, monitoring,
  route audit, cache traps, incidents and what they taught
- **[Configuration](docs/CONFIGURATION.md)** — complete variable reference
- **[SSL Setup](docs/SSL_SETUP.md)** — all SSL modes and reverse proxy setup
- **[Database Setup](docs/DATABASE_SETUP.md)** — PostgreSQL configuration
- **[Troubleshooting](docs/TROUBLESHOOTING.md)** — common issues and solutions
- **[User Management](docs/AUTHELIA_USER_MANAGEMENT.md)** — groups and permissions
- **[Token Sharing](docs/TOKEN_SHARING.md)** — external sharing workflow
- **[Auth-Service](docs/AUTH_SERVICE.md)** — the service's internals
- **[Nginx](docs/NGINX_CONFIGURATION.md)** — reverse proxy details
- **[Cloudflare Tunnel](docs/CLOUDFLARE_TUNNEL.md)** — exposure without opening a port

## Troubleshooting

```bash
docker compose ps                                  # is everything up
docker compose logs -f                             # follow the logs
docker exec orthanc-nginx ls -la /etc/nginx/ssl/   # certificates
```

| Symptom | Cause, usually |
|---|---|
| A user cannot log in | Check the account in the panel's *Users* tab — disabled, or wrong group. Authelia reloads on its own; restarting it changes nothing. |
| Everyone gets 401 at once | Authelia only reads `configuration.yml` at **startup**. If it was edited, check the container's start time before looking anywhere else. |
| Orthanc cannot reach PostgreSQL | `POSTGRES_PASSWORD` in `.env` no longer matches the one the volume was created with — it is only read when the volume is first initialised |
| Port conflict | Change the ports in `docker-compose.yml` |
| SSL warning | Expected with a self-signed certificate |

See the [Troubleshooting Guide](docs/TROUBLESHOOTING.md), and
[Operations](docs/EXPLOITATION.md) for the incidents worth knowing about before
they happen to you.

## Rescue paths

For when the panel itself cannot be reached. Normal administration does not go
through these.

```bash
./manage-authelia-users.sh          # edit the user database directly
./scripts/reset-admin-password.sh   # reset an administrator's password
./scripts/apply.sh                  # redeploy and verify every route
./scripts/backup-postgres.sh        # dump PostgreSQL, then verify the dump
```

## Enabled Orthanc plugins

PostgreSQL (storage and index), DICOMweb, Authorization, Explorer 2, Stone Web
Viewer, VolView, Housekeeper, GDCM.

## Sources and acknowledgments

Built upon excellent open-source projects:

- **Orthanc PACS** — Sébastien Jodogne, UCLouvain — [orthanc-server.com](https://orthanc-server.com)
- **Authelia** — modern authentication server
- **OHIF Viewer** — Open Health Imaging Foundation
- **PostgreSQL** — high-performance database
- **Redis** — in-memory data store

Forked and enhanced by **yokoinc** for the open-source medical imaging community.
