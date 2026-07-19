"""
Tests d'integration : endpoints FastAPI + Redis + fichiers YAML/JSON + mocks httpx.

Utilise :
- TestClient (starlette) pour appeler les endpoints
- fakeredis.aioredis pour simuler Redis en memoire
- respx pour mocker les appels a http://orthanc:8042/tools/reset
- tmp_path pour isoler les fichiers authelia.yml + orthanc.json + backups

Executer :
    cd services/auth-service/sources
    python -m pytest tests/test_integration.py -v
"""

import json
import threading
import time
from pathlib import Path

import fakeredis.aioredis
import httpx
import pytest
import respx
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from filelock import FileLock

import admin_module


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
    """Redirige les 3 chemins module-level vers un tmp_path per-test."""
    authelia = tmp_path / "authelia.yml"
    orthanc = tmp_path / "orthanc.json"
    backups = tmp_path / "backups"
    monkeypatch.setattr(admin_module, "AUTHELIA_YML", authelia)
    monkeypatch.setattr(admin_module, "ORTHANC_JSON", orthanc)
    monkeypatch.setattr(admin_module, "BACKUPS_DIR", backups)
    return {"authelia": authelia, "orthanc": orthanc, "backups": backups}


@pytest.fixture
def fake_redis():
    """Injecte un Redis fake dans le module (synchrone pour TestClient)."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    admin_module.set_redis(r)
    return r


@pytest.fixture
def admin_user():
    return admin_module.AdminUser(username="cuffel.gregory", groups=["admins"])


@pytest.fixture
def app(admin_user):
    """FastAPI app avec le router + middlewares wire-up."""
    app = FastAPI()
    app.include_router(admin_module.router)
    app.middleware("http")(admin_module.setup_gate)
    app.middleware("http")(admin_module.csrf_gate)
    # Override du dependency : pas de vraie auth Authelia en test
    app.dependency_overrides[admin_module.require_admin] = lambda: admin_user
    return app


@pytest.fixture
def client(app, tmp_paths, fake_redis):
    return TestClient(app)


@pytest.fixture
def csrf_headers(client):
    """Setup double-submit cookie + header pour passer csrf_gate."""
    client.cookies.set("orthanc_admin_csrf", "test-token")
    return {"x-csrf-token": "test-token"}


@pytest.fixture
def valid_orthanc_json(tmp_paths):
    """Pre-cree un orthanc.json valide (avec les flags DB critiques)."""
    initial = {
        "Name": "Cuffel PACS",
        "DicomAet": "YOKOINC",
        "DicomModalitiesInDatabase": True,
        "OrthancPeersInDatabase": True,
        "DicomPort": 4242,
        "HttpPort": 8042,
    }
    tmp_paths["orthanc"].write_text(json.dumps(initial, indent=2))
    return initial


@pytest.fixture
def valid_authelia_yml(tmp_paths):
    """Pre-cree un users_database.yml valide (1 admin actif, argon2id)."""
    hasher = admin_module._hasher
    data = {
        "users": {
            "cuffel.gregory": {
                "disabled": False,
                "displayname": "Gregory Cuffel",
                "email": "cuffel.gregory@gmail.com",
                "password": hasher.hash("initial-admin-password"),
                "groups": ["admins", "doctors"],
            },
        },
    }
    tmp_paths["authelia"].write_text(
        yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
    )
    return data


# ============================================================================
# Test 1 : Setup wizard end-to-end
# ============================================================================

class TestSetupWizard:

    def test_full_flow(self, client, tmp_paths, fake_redis):
        """Redis vide → create admin → finalize → 2eme create bloque par middleware."""
        # Etat initial : setup_completed absent
        # (fake_redis est frais, aucune clef)

        # Etape 1 : creer le premier admin
        r = client.post("/auth/setup/create-admin", json={
            "username": "cuffel.gregory",
            "displayname": "Gregory Cuffel",
            "email": "cuffel.gregory@gmail.com",
            "password": "premier-admin-12345",
            "groups": ["admins"],
        })
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

        # Le YAML doit exister, contenir l'user avec un hash argon2id
        assert tmp_paths["authelia"].exists()
        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert "cuffel.gregory" in yml["users"]
        assert yml["users"]["cuffel.gregory"]["password"].startswith("$argon2id$")
        assert "admins" in yml["users"]["cuffel.gregory"]["groups"]

        # Etape 2 : finaliser
        r = client.post("/auth/setup/finalize")
        assert r.status_code == 200
        assert r.json()["admins"] == ["cuffel.gregory"]

        # Redis a bien le flag maintenant
        import asyncio
        val = asyncio.run(fake_redis.get("orthanc_authelia:setup_completed"))
        assert val == "1"

        # Etape 3 : 2eme appel bloque par setup_gate (redirect vers /auth/admin)
        r = client.post("/auth/setup/create-admin", json={
            "username": "someone.else",
            "displayname": "Someone Else",
            "email": "someone@example.com",
            "password": "another-password-12345",
        }, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/auth/admin"

    def test_finalize_refused_without_admin(self, client, tmp_paths, fake_redis):
        """Finaliser sans admin actif = 400 (invariant lockout)."""
        # Pas de POST create-admin avant
        r = client.post("/auth/setup/finalize")
        assert r.status_code == 400
        assert "admin" in r.text.lower()

    def test_create_admin_forces_admins_group(self, client, tmp_paths, fake_redis):
        """Meme si l'user oublie 'admins' dans groups, on l'ajoute."""
        r = client.post("/auth/setup/create-admin", json={
            "username": "cuffel.gregory",
            "displayname": "Gregory",
            "email": "cuffel@example.com",
            "password": "long-password-1234",
            "groups": ["doctors"],  # PAS admins
        })
        assert r.status_code == 200
        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert "admins" in yml["users"]["cuffel.gregory"]["groups"]


# ============================================================================
# Test 2 : CF Access rotate + verify pipeline
# ============================================================================

class TestCFAccess:

    def test_rotate_then_verify_matches(self, client, fake_redis, csrf_headers):
        """POST rotate → GET verify-cf avec nouveaux headers = 204."""
        r = client.post("/api/admin/cf-access/rotate", json={
            "client_id": "new-id-ec87a9cb.access",
            "client_secret": "s" * 64,
        }, headers=csrf_headers)
        assert r.status_code == 200

        # Verify avec les nouveaux headers
        r = client.get("/api/internal/verify-cf", headers={
            "x-cf-client-id": "new-id-ec87a9cb.access",
            "x-cf-client-secret": "s" * 64,
        })
        assert r.status_code == 204

    def test_verify_wrong_secret_rejected(self, client, fake_redis, csrf_headers):
        """Verify avec mauvais secret = 403."""
        # Rotate d'abord (client_id min 10 chars par Field validation)
        client.post("/api/admin/cf-access/rotate", json={
            "client_id": "id-abc-with-length.access",
            "client_secret": "s" * 64,
        }, headers=csrf_headers)

        # Mauvais secret
        r = client.get("/api/internal/verify-cf", headers={
            "x-cf-client-id": "id-abc-with-length.access",
            "x-cf-client-secret": "w" * 64,
        })
        assert r.status_code == 403

    def test_verify_no_config_returns_503(self, client, fake_redis):
        """Verify sur Redis vide = 503 (pas configure)."""
        r = client.get("/api/internal/verify-cf", headers={
            "x-cf-client-id": "any",
            "x-cf-client-secret": "any",
        })
        assert r.status_code == 503

    def test_rotate_snapshots_old_to_history(self, client, fake_redis, csrf_headers):
        """Ancien couple pousse dans cf_access:history au moment du rotate."""
        # 1er rotate (client_id min 10 chars)
        r1 = client.post("/api/admin/cf-access/rotate", json={
            "client_id": "id-one-abc.access",
            "client_secret": "1" * 64,
        }, headers=csrf_headers)
        assert r1.status_code == 200, r1.text
        # 2eme rotate
        r2 = client.post("/api/admin/cf-access/rotate", json={
            "client_id": "id-two-abc.access",
            "client_secret": "2" * 64,
        }, headers=csrf_headers)
        assert r2.status_code == 200, r2.text

        # History contient au moins l'ancien couple
        import asyncio
        length = asyncio.run(fake_redis.llen("cf_access:history"))
        assert length >= 1
        first = asyncio.run(fake_redis.lindex("cf_access:history", 0))
        assert "id-one-abc.access" in first
        assert "1" * 64 in first


# ============================================================================
# Test 3 : Orthanc config change + reload
# ============================================================================

class TestOrthancConfig:

    def test_patch_writes_file_and_calls_reset(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """PATCH → JSON updated on disk + POST /tools/reset called + audit."""
        with respx.mock(base_url="http://orthanc:8042") as mock:
            reset_route = mock.post("/tools/reset").respond(status_code=200, json={})

            r = client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "New PACS Name", "HttpCompressionEnabled": True},
            }, headers=csrf_headers)
            assert r.status_code == 200, r.text
            assert reset_route.called

        # Le fichier a ete mis a jour
        new = json.loads(tmp_paths["orthanc"].read_text())
        assert new["Name"] == "New PACS Name"
        assert new["HttpCompressionEnabled"] is True
        # Flags critiques preserves
        assert new["DicomModalitiesInDatabase"] is True
        assert new["OrthancPeersInDatabase"] is True

        # Un backup a ete cree
        backups = list(tmp_paths["backups"].glob("orthanc.json.bak.*"))
        assert len(backups) == 1

        # Audit stream a une entree
        import asyncio
        entries = asyncio.run(fake_redis.xrange("admin:audit"))
        assert len(entries) >= 1
        _, fields = entries[-1]
        assert fields["event"] == "orthanc.config.updated"
        assert fields["actor"] == "cuffel.gregory"

    def test_patch_refuses_non_whitelisted_path(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """Un chemin hors whitelist renvoie 400."""
        r = client.patch("/api/admin/orthanc/config", json={
            "changes": {"PostgreSQL.Password": "hack"},
        }, headers=csrf_headers)
        assert r.status_code == 400
        assert "non editable" in r.text.lower()

    def test_patch_refuses_disabling_critical_flag(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """Desactiver DicomModalitiesInDatabase = 400."""
        r = client.patch("/api/admin/orthanc/config", json={
            "changes": {"DicomModalitiesInDatabase": False},
        }, headers=csrf_headers)
        assert r.status_code == 400


# ============================================================================
# Test 4 : Rollback via /api/admin/backups/restore
# ============================================================================

class TestBackupRestore:

    def test_orthanc_rollback(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """PATCH puis restore = fichier remis a l'etat initial."""
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.post("/tools/reset").respond(status_code=200, json={})

            # Modif
            client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "Modified"},
            }, headers=csrf_headers)
            assert json.loads(tmp_paths["orthanc"].read_text())["Name"] == "Modified"

            # Recuperer le nom du backup cree
            backups = sorted(tmp_paths["backups"].glob("orthanc.json.bak.*"))
            assert backups
            backup_name = backups[0].name

            # Restore
            r = client.post(
                f"/api/admin/backups/restore?backup_name={backup_name}",
                headers=csrf_headers,
            )
            assert r.status_code == 200, r.text

        # Le fichier est bien revenu au Name initial
        restored = json.loads(tmp_paths["orthanc"].read_text())
        assert restored["Name"] == valid_orthanc_json["Name"]

    def test_restore_rejects_bad_name(self, client, tmp_paths, fake_redis, csrf_headers):
        """Nom sans .bak. dedans = 404."""
        r = client.post(
            "/api/admin/backups/restore?backup_name=evil_traversal",
            headers=csrf_headers,
        )
        assert r.status_code == 404


# ============================================================================
# Test 5 : CSRF rejection
# ============================================================================

class TestCSRF:

    def test_post_without_token_refused(self, client, tmp_paths, fake_redis):
        """POST /api/admin/* sans cookie + header CSRF = 403."""
        r = client.post("/api/admin/cf-access/rotate", json={
            "client_id": "any-id-here",
            "client_secret": "s" * 64,
        })
        assert r.status_code == 403
        assert "csrf.token" in r.text

    def test_post_with_mismatched_token_refused(self, client, tmp_paths, fake_redis):
        """Cookie != header = 403."""
        client.cookies.set("orthanc_admin_csrf", "one-token")
        r = client.post("/api/admin/cf-access/rotate", json={
            "client_id": "id",
            "client_secret": "s" * 64,
        }, headers={"x-csrf-token": "other-token"})
        assert r.status_code == 403
        assert "csrf.token" in r.text

    def test_get_bypass_csrf(self, client, tmp_paths, fake_redis):
        """GET n'est jamais soumis a CSRF (idempotent)."""
        # GET /api/admin/cf-access sans cookie
        r = client.get("/api/admin/cf-access")
        assert r.status_code == 200  # OK, csrf_gate laisse passer

    def test_internal_verify_bypass_csrf(self, client, fake_redis):
        """/api/internal/* n'est pas /api/admin/* et bypasse."""
        r = client.get("/api/internal/verify-cf", headers={
            "x-cf-client-id": "x", "x-cf-client-secret": "y",
        })
        # 503 (pas configure) prouve qu'on a atteint l'endpoint, pas 403 CSRF
        assert r.status_code == 503


# ============================================================================
# Test 6 : File lock — concurrence write orthanc.json
# ============================================================================

class TestFileLock:

    def test_concurrent_write_returns_423(
        self, client, tmp_paths, fake_redis, csrf_headers,
        valid_orthanc_json, monkeypatch,
    ):
        """
        Un thread externe tient le lock, la requete API attend puis timeout → 423.
        Reduit le timeout admin_module a 1s pour ne pas ralentir le test.
        """
        # Patch le timeout FileLock pour aller vite
        orig_flock = admin_module.FileLock

        def fast_flock(path, timeout=None):
            return orig_flock(path, timeout=1)  # 1s au lieu de 5s

        monkeypatch.setattr(admin_module, "FileLock", fast_flock)

        lock_path = str(tmp_paths["orthanc"]) + ".lock"
        barrier = threading.Barrier(2)

        def hold_lock():
            with orig_flock(lock_path, timeout=5):
                barrier.wait()  # signale au test qu'on tient le lock
                time.sleep(3)   # hold plus longtemps que le timeout endpoint

        holder = threading.Thread(target=hold_lock)
        holder.start()
        try:
            barrier.wait()  # attend que hold_lock ait le lock

            # Maintenant tente d'ecrire via l'API
            r = client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "should not succeed"},
            }, headers=csrf_headers)
            assert r.status_code == 423
            assert "verrouille" in r.text.lower()
        finally:
            holder.join()

        # Le fichier n'a PAS ete modifie (le lock a empeche l'ecriture)
        content = json.loads(tmp_paths["orthanc"].read_text())
        assert content["Name"] == valid_orthanc_json["Name"]
