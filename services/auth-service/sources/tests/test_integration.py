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

import fakeredis.aioredis
import httpx
import pytest
import respx
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
    env = tmp_path / ".env"
    authelia_cfg = tmp_path / "configuration.yml"
    monkeypatch.setattr(admin_module, "ENV_FILE", env)
    monkeypatch.setattr(admin_module, "AUTHELIA_CONFIG", authelia_cfg)
    return {
        "authelia": authelia, "orthanc": orthanc, "backups": backups,
        "env": env, "authelia_cfg": authelia_cfg,
    }


@pytest.fixture
def fake_redis():
    """Inject a fake Redis into the module, plus a synchronous handle.

    Both clients share the same FakeServer: what one writes, the other sees.
    Application code uses the async client; tests go through `.sync` to set
    up or read back state.

    Do not go back to asyncio.run() on the async client: asyncio.run opens
    an event loop then closes it, the fakeredis client stays bound to that
    dead loop, and the next HTTP request -- served by the TestClient's
    portal, so another loop -- breaks with "Queue is bound to a different
    event loop". The failure says nothing about the code under test.
    """
    server = fakeredis.FakeServer()
    r = fakeredis.aioredis.FakeRedis(decode_responses=True, server=server)
    r.sync = fakeredis.FakeRedis(decode_responses=True, server=server)
    admin_module.set_redis(r)
    return r


@pytest.fixture
def admin_user():
    return admin_module.AdminUser(username="cuffel.gregory", groups=["admin"])


@pytest.fixture
def app(admin_user):
    """FastAPI app with the router and middlewares wired up."""
    app = FastAPI()
    app.include_router(admin_module.router)
    app.middleware("http")(admin_module.setup_gate)
    app.middleware("http")(admin_module.csrf_gate)
    # Dependency override: no real Authelia auth in tests
    app.dependency_overrides[admin_module.require_admin] = lambda: admin_user
    return app


@pytest.fixture
def client(app, tmp_paths, fake_redis):
    """TestClient kept open for the whole duration of the test.

    Without a context manager, Starlette opens then closes an anyio portal
    -- hence an event loop -- on EVERY request. The `with` keeps a single
    portal, which is what allows the fakeredis client to stay usable from
    one request to the next.
    """
    with TestClient(app) as c:
        yield c


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
                "groups": ["admin", "doctor"],
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
        # (fake_redis is fresh, no keys)

        # Step 1: create the first administrator
        r = client.post("/setup/create-admin", json={
            "username": "cuffel.gregory",
            "displayname": "Gregory Cuffel",
            "email": "cuffel.gregory@gmail.com",
            "password": "premier-admin-12345",
            "groups": ["admin"],
        })
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

        # The YAML must exist and hold the user with an argon2id hash
        assert tmp_paths["authelia"].exists()
        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert "cuffel.gregory" in yml["users"]
        assert yml["users"]["cuffel.gregory"]["password"].startswith("$argon2id$")
        assert "admin" in yml["users"]["cuffel.gregory"]["groups"]

        # Etape 2 : finaliser
        r = client.post("/setup/finalize")
        assert r.status_code == 200
        assert r.json()["admins"] == ["cuffel.gregory"]

        # Redis now carries the flag
        val = fake_redis.sync.get("orthanc_authelia:setup_completed")
        assert val == "1"

        # Step 3: a second call is refused. The middleware only redirects
        # SPA pages; an API must answer a JSON error, not a 302.
        r = client.post("/setup/create-admin", json={
            "username": "someone.else",
            "displayname": "Someone Else",
            "email": "someone@example.com",
            "password": "another-password-12345",
        })
        assert r.status_code == 409
        # No assertion on the text: it follows the interface language.
        # The 409 is what forms the contract.

    def _bootstrap_only(self, tmp_paths):
        """Database as bootstrap.sh leaves it on a fresh installation."""
        tmp_paths["authelia"].write_text(yaml.safe_dump({
            "users": {
                admin_module.BOOTSTRAP_USERNAME: {
                    "disabled": True,
                    "displayname": "Compte d'amorcage",
                    "password": "$argon2id$v=19$m=65536,t=3,p=4$nimportequoi",
                    "email": "bootstrap@localhost",
                    "groups": [],
                },
            },
        }))

    def _create_first_admin(self, client):
        return client.post("/setup/create-admin", json={
            "username": "cuffel.gregory",
            "displayname": "Gregory Cuffel",
            "email": "cuffel.gregory@gmail.com",
            "password": "premier-admin-12345",
            "groups": ["admin"],
        })

    def test_finalize_removes_bootstrap_account(self, client, tmp_paths, fake_redis):
        """The bootstrap account disappears once the real administrator exists.

        Authelia refuses to start on an empty database, hence this account in
        the template. It must not survive installation: once the wizard is
        finished, only the operator's account remains.
        """
        self._bootstrap_only(tmp_paths)

        assert self._create_first_admin(client).status_code == 200

        # Still present just before finalisation
        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert admin_module.BOOTSTRAP_USERNAME in yml["users"]

        r = client.post("/setup/finalize")
        assert r.status_code == 200, r.text
        assert r.json()["bootstrap_removed"] == admin_module.BOOTSTRAP_USERNAME

        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert list(yml["users"]) == ["cuffel.gregory"]

    def test_finalize_without_bootstrap_account(self, client, tmp_paths, fake_redis):
        """Absent ou renomme, la finalisation se passe sans rien supprimer."""
        assert self._create_first_admin(client).status_code == 200

        r = client.post("/setup/finalize")
        assert r.status_code == 200, r.text
        assert r.json()["bootstrap_removed"] is None

        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert list(yml["users"]) == ["cuffel.gregory"]

    def test_finalize_refused_without_admin(self, client, tmp_paths, fake_redis):
        """Finaliser sans admin actif = 400 (invariant lockout)."""
        # No create-admin POST beforehand
        r = client.post("/setup/finalize")
        assert r.status_code == 400
        assert "admin" in r.text.lower()

    def test_create_admin_forces_admins_group(self, client, tmp_paths, fake_redis):
        """Even if the user forgets 'admins' in groups, we add it."""
        r = client.post("/setup/create-admin", json={
            "username": "cuffel.gregory",
            "displayname": "Gregory",
            "email": "cuffel@example.com",
            "password": "long-password-1234",
            "groups": ["doctors"],  # PAS admins
        })
        assert r.status_code == 200
        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert "admin" in yml["users"]["cuffel.gregory"]["groups"]


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

        # The audit stream holds one entry
        entries = fake_redis.sync.xrange("admin:audit")
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

            # Fetch the name of the backup created
            backups = sorted(tmp_paths["backups"].glob("orthanc.json.bak.*"))
            assert backups
            backup_name = backups[0].name

            # Restore
            r = client.post(
                f"/api/admin/backups/restore?backup_name={backup_name}",
                headers=csrf_headers,
            )
            assert r.status_code == 200, r.text

        # The file is back to its original Name
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

    def test_post_without_token_refused(self, client, tmp_paths, fake_redis, valid_authelia_yml):
        """POST /api/admin/* sans cookie + header CSRF = 403."""
        r = client.post("/api/admin/users", json={
            "username": "csrf.victim",
            "displayname": "CSRF Test",
            "email": "csrf@example.com",
            "password": "long-enough-password-123",
        })
        assert r.status_code == 403
        assert "csrf.token" in r.text

    def test_post_with_mismatched_token_refused(self, client, tmp_paths, fake_redis, valid_authelia_yml):
        """Cookie != header = 403."""
        client.cookies.set("orthanc_admin_csrf", "one-token")
        r = client.post("/api/admin/users", json={
            "username": "csrf.victim",
            "displayname": "CSRF Test",
            "email": "csrf@example.com",
            "password": "long-enough-password-123",
        }, headers={"x-csrf-token": "other-token"})
        assert r.status_code == 403
        assert "csrf.token" in r.text

    def test_get_bypass_csrf(self, client, tmp_paths, fake_redis, valid_authelia_yml):
        """GET n'est jamais soumis a CSRF (idempotent)."""
        r = client.get("/api/admin/users")
        assert r.status_code == 200  # OK, csrf_gate laisse passer

# ============================================================================
# Test 6 : File lock — concurrence write orthanc.json
# ============================================================================

class TestFileLock:

    def test_concurrent_write_returns_423(
        self, client, tmp_paths, fake_redis, csrf_headers,
        valid_orthanc_json, monkeypatch,
    ):
        """
        An external thread holds the lock, the API request waits then times
        out with a 423. The admin_module timeout is lowered to 1s to keep the
        test fast.
        """
        # Patch the FileLock timeout to keep the test fast
        orig_flock = admin_module.FileLock

        def fast_flock(path, timeout=None):
            return orig_flock(path, timeout=1)  # 1s instead of 5s

        monkeypatch.setattr(admin_module, "FileLock", fast_flock)

        lock_path = str(tmp_paths["orthanc"]) + ".lock"
        barrier = threading.Barrier(2)

        def hold_lock():
            with orig_flock(lock_path, timeout=5):
                barrier.wait()  # tells the test we hold the lock
                time.sleep(3)   # hold longer than the endpoint timeout

        holder = threading.Thread(target=hold_lock)
        holder.start()
        try:
            barrier.wait()  # wait for hold_lock to take the lock

            # Maintenant tente d'ecrire via l'API
            r = client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "should not succeed"},
            }, headers=csrf_headers)
            assert r.status_code == 423
            # The text follows the interface language; the 423 is the contract.
        finally:
            holder.join()

        # The file was NOT modified (the lock prevented the write)
        content = json.loads(tmp_paths["orthanc"].read_text())
        assert content["Name"] == valid_orthanc_json["Name"]


# ============================================================================
# Test 7 : Auto-rollback Orthanc quand /tools/reset echoue
# ============================================================================

class TestAutoRollback:

    def test_rollback_on_reset_failure(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """PATCH → /tools/reset renvoie 500 → rollback auto → 502 mais fichier restore."""
        # First reset (the failing one) then second (the rollback, succeeding)
        with respx.mock(base_url="http://orthanc:8042") as mock:
            reset_route = mock.post("/tools/reset").mock(
                side_effect=[
                    httpx.Response(500, text="orthanc down"),  # 1er appel = KO
                    httpx.Response(200, json={}),               # 2eme = rollback OK
                ]
            )

            r = client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "would-fail"},
            }, headers=csrf_headers)

            assert r.status_code == 502
            assert "rollback" in r.text.lower()
            # The mock was indeed called twice (initial + rollback)
            assert reset_route.call_count == 2

        # The file is back to its original Name (rollback done)
        current = json.loads(tmp_paths["orthanc"].read_text())
        assert current["Name"] == valid_orthanc_json["Name"]

        # The audit trail shows the rollback
        entries = fake_redis.sync.xrange("admin:audit")
        events = [f["event"] for _, f in entries]
        assert "orthanc.config.rolled_back" in events


# ============================================================================
# Test 8 : Setup wizard verrouille apres 1er create-admin
# ============================================================================

class TestSetupLockout:

    def test_second_create_admin_refused(self, client, tmp_paths, fake_redis):
        """After one create-admin, a second call = 409 until finalised."""
        r1 = client.post("/setup/create-admin", json={
            "username": "first.admin",
            "displayname": "First",
            "email": "first@example.com",
            "password": "premier-admin-1234",
        })
        assert r1.status_code == 200

        r2 = client.post("/setup/create-admin", json={
            "username": "second.admin",
            "displayname": "Second",
            "email": "second@example.com",
            "password": "second-admin-12345",
        })
        assert r2.status_code == 409
        assert "deja ete cree" in r2.text.lower()

    def test_finalize_clears_lock_next_setup_impossible_anyway(
        self, client, tmp_paths, fake_redis,
    ):
        """After finalize the first_admin lock is removed (setup_gate closes all)."""
        client.post("/setup/create-admin", json={
            "username": "admin.one",
            "displayname": "Admin",
            "email": "a@b.com",
            "password": "admin-password-1234",
        })
        client.post("/setup/finalize")

        first_admin_flag = fake_redis.sync.get("orthanc_authelia:setup_first_admin_created")
        setup_flag = fake_redis.sync.get("orthanc_authelia:setup_completed")
        assert first_admin_flag is None
        assert setup_flag == "1"


# ============================================================================
# ============================================================================
# Test 10: corrupt YAML/JSON -> readable 500 with a restore hint
# ============================================================================

class TestCorruptConfig:

    def test_corrupt_authelia_yml_returns_readable_500(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        """Syntactically broken YAML -> 500 with a message hinting at the restore."""
        tmp_paths["authelia"].write_text("users:\n  cuffel: {this is: not: valid: yaml")

        r = client.get("/api/admin/users")
        assert r.status_code == 500
        # The text follows the interface language; the 500 and the mention
        # of restoring are the contract.
        assert "backups" in r.text.lower()

    def test_corrupt_orthanc_json_returns_readable_500(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        """JSON syntaxiquement casse → 500 avec message restore."""
        tmp_paths["orthanc"].write_text('{"Name": "unclosed')

        r = client.get("/api/admin/orthanc/config")
        assert r.status_code == 500
        # The text follows the interface language; the 500 and the mention
        # of restoring are the contract.
        assert "backups" in r.text.lower()


# ============================================================================
# Test 11 : Health endpoint
# ============================================================================

class TestHealth:

    def test_health_reports_component_status(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, valid_orthanc_json,
    ):
        """/api/admin/health renvoie l'etat de chaque composant."""
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.get("/system").respond(status_code=200, json={"Version": "26.4.2"})

            r = client.get("/api/admin/health")
            assert r.status_code == 200
            checks = r.json()["checks"]
            assert set(checks.keys()) == {"redis", "authelia_yml", "orthanc_json", "orthanc_api"}
            assert checks["redis"]["ok"] is True
            assert checks["authelia_yml"]["ok"] is True
            assert checks["orthanc_json"]["ok"] is True
            assert checks["orthanc_api"]["ok"] is True

    def test_ui_redirects_to_setup_when_not_done(
        self, client, tmp_paths, fake_redis,
    ):
        """The hub redirects to the wizard until the installation is done."""
        r = client.get("/ui/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/console/setup"

    def test_ui_setup_redirects_to_hub_when_done(
        self, client, tmp_paths, fake_redis,
    ):
        """Once finalised, the wizard redirects to the hub."""
        fake_redis.sync.set("orthanc_authelia:setup_completed", "1")

        r = client.get("/ui/setup", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/console/"

    def test_ui_assets_never_redirected(
        self, client, tmp_paths, fake_redis,
    ):
        """Assets escape the middleware, otherwise the SPA does not load."""
        r = client.get("/ui/assets/index-abc123.js", follow_redirects=False)
        assert r.status_code != 302

    def test_whoami_returns_info_and_csrf_cookie(
        self, client, tmp_paths, fake_redis, valid_authelia_yml,
    ):
        """whoami fournit l'identite au SPA et pose le cookie CSRF."""
        import json
        fake_redis.sync.set("orthanc_authelia:setup_completed", "1")

        r = client.get("/api/admin/whoami")
        assert r.status_code == 200
        data = json.loads(r.text)
        assert "username" in data
        assert "image_version" in data
        assert "orthanc_admin_csrf" in r.cookies
        assert len(r.cookies["orthanc_admin_csrf"]) >= 40

    def test_health_reports_corrupt_orthanc_json(
        self, client, tmp_paths, fake_redis, valid_authelia_yml,
    ):
        """orthanc.json corrompu = health signale KO sur ce composant."""
        tmp_paths["orthanc"].write_text('{"unclosed')

        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.get("/system").respond(status_code=200, json={})

            r = client.get("/api/admin/health")
            assert r.status_code == 200
            assert r.json()["checks"]["orthanc_json"]["ok"] is False


# ============================================================================
# Backup restore: the name comes from the client
# ============================================================================

class TestBackupRestoreSafety:

    def test_path_traversal_refused(self, client, tmp_paths, fake_redis, csrf_headers):
        """A name climbing out of the backups directory must be refused.

        "orthanc.json.bak.../../../x" satisfies the shape checks (contains
        .bak., starts with orthanc.json.bak.) while designating a file outside
        the directory.
        """
        r = client.post(
            "/api/admin/backups/restore",
            params={"backup_name": "orthanc.json.bak.../../../etc/passwd"},
            headers=csrf_headers,
        )
        assert r.status_code == 400
        # The text follows the interface language; the 400 is the contract.

    def test_absolute_path_refused(self, client, tmp_paths, fake_redis, csrf_headers):
        r = client.post(
            "/api/admin/backups/restore",
            params={"backup_name": "/etc/orthanc.json.bak.1"},
            headers=csrf_headers,
        )
        assert r.status_code == 400

    def test_legitimate_name_still_accepted(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """Un nom normal continue de fonctionner."""
        import respx, httpx
        tmp_paths["backups"].mkdir(parents=True, exist_ok=True)
        backup = tmp_paths["backups"] / "orthanc.json.bak.20260101-000000"
        backup.write_text(tmp_paths["orthanc"].read_text())

        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.post("/tools/reset").respond(status_code=200, json={})
            r = client.post(
                "/api/admin/backups/restore",
                params={"backup_name": backup.name},
                headers=csrf_headers,
            )
        assert r.status_code == 200, r.text


# ============================================================================
# Orthanc reload unavailable: the change must survive
# ============================================================================

class TestOrthancReloadRefused:

    def test_403_keeps_change_and_asks_restart(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """A 403 from the plugin must not undo the write.

        The file is valid, only the hot reload is refused: undoing would lose
        the user's input for no reason.
        """
        import respx, httpx, json as _json
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.post("/tools/reset").respond(status_code=403)
            r = client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "Nouveau Nom"},
            }, headers=csrf_headers)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["restart_required"] is True
        assert "restart orthanc" in body["message"].lower()

        # The change did reach the disk
        assert _json.loads(tmp_paths["orthanc"].read_text())["Name"] == "Nouveau Nom"

    def test_network_error_still_rolls_back(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """A network failure is still handled by a rollback: Orthanc may be in an
        uncertain state, unlike the case of an explicit refusal."""
        import respx, httpx, json as _json
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.post("/tools/reset").mock(
                side_effect=httpx.ConnectError("injoignable")
            )
            r = client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "Must Not Remain"},
            }, headers=csrf_headers)

        assert r.status_code == 502
        assert _json.loads(tmp_paths["orthanc"].read_text())["Name"] == valid_orthanc_json["Name"]


# ============================================================================
# URL publique
# ============================================================================

CONFIG_TYPE = """\
# Access rules -- comment that must survive
access_control:
  rules:
    - domain: pacs.localhost
      policy: bypass
    - domain: pacs.localhost
      policy: one_factor
session:
  cookies:
    - domain: pacs.localhost   # cookie domain, without a port
      authelia_url: https://pacs.localhost:30443/auth
      default_redirection_url: https://pacs.localhost:30443/ui/app/
"""


class TestPublicUrl:

    def _prepare(self, tmp_paths):
        tmp_paths["env"].write_text(
            "TZ=Europe/Paris\n"
            "PUBLIC_URL=https://pacs.localhost:30443\n"
            "LOG_LEVEL=INFO\n"
        )
        tmp_paths["authelia_cfg"].write_text(CONFIG_TYPE)

    def test_change_updates_env_and_authelia(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        """L'URL publique se propage au .env et a toute la config Authelia."""
        self._prepare(tmp_paths)

        r = client.post(
            "/api/admin/network",
            json={"public_url": "https://pacs.exemple.fr"},
            headers=csrf_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["restart_required"] is True

        env = tmp_paths["env"].read_text()
        assert "PUBLIC_URL=https://pacs.exemple.fr" in env
        # Les autres variables survivent
        assert "TZ=Europe/Paris" in env
        assert "LOG_LEVEL=INFO" in env

        cfg = tmp_paths["authelia_cfg"].read_text()
        assert "pacs.localhost" not in cfg
        assert cfg.count("domain: pacs.exemple.fr") == 3
        assert "authelia_url: https://pacs.exemple.fr/auth" in cfg
        # The port disappears along with the previous origin
        assert ":30443" not in cfg
        # Comments are preserved: a YAML round-trip would lose them
        assert "comment that must survive" in cfg
        assert "without a port" in cfg

    def test_same_url_touches_nothing(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        """Reappliquer l'URL courante est un no-op, sans sauvegarde inutile."""
        self._prepare(tmp_paths)
        before = tmp_paths["authelia_cfg"].read_text()

        r = client.post(
            "/api/admin/network",
            json={"public_url": "https://pacs.localhost:30443"},
            headers=csrf_headers,
        )
        assert r.status_code == 200
        assert r.json()["unchanged"] is True
        assert tmp_paths["authelia_cfg"].read_text() == before

    def test_missing_env_answers_503_with_next_steps(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        """Without the .env mount, the error explains what to do."""
        tmp_paths["authelia_cfg"].write_text(CONFIG_TYPE)
        # .env volontairement absent

        r = client.post(
            "/api/admin/network",
            json={"public_url": "https://pacs.exemple.fr"},
            headers=csrf_headers,
        )
        assert r.status_code == 500
        assert "PUBLIC_URL" in r.text

    def test_hand_edited_authelia_config_aborts(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        """When the previous domain cannot be found, we do not guess: we abort."""
        tmp_paths["env"].write_text("PUBLIC_URL=https://pacs.localhost:30443\n")
        tmp_paths["authelia_cfg"].write_text("session:\n  cookies: []\n")

        r = client.post(
            "/api/admin/network",
            json={"public_url": "https://pacs.exemple.fr"},
            headers=csrf_headers,
        )
        assert r.status_code == 500
        assert "modifie a la main" in r.text
        # .env has not moved: nothing is half-applied
        assert "PUBLIC_URL=https://pacs.localhost:30443" in tmp_paths["env"].read_text()

    def test_rejections(self, client, tmp_paths, fake_redis, csrf_headers):
        """https obligatoire, origine seule, hote pointe."""
        self._prepare(tmp_paths)
        for bad_url in [
            "http://pacs.exemple.fr",
            "https://monpacs",
            "https://pacs.exemple.fr/chemin",
        ]:
            r = client.post(
                "/api/admin/network",
                json={"public_url": bad_url},
                headers=csrf_headers,
            )
            assert r.status_code == 400, f"{bad_url} aurait du etre refusee"


class TestModalities:
    """DICOM devices: declaration, removal, connectivity test.

    These routes go through Orthanc's API, simulated here. They were only
    covered by the end-to-end test, run by hand: CI only executes the unit
    tests, so a change breaking them would have gone through green.
    """

    def test_list_gathers_configurations(
        self, client, tmp_paths, fake_redis, valid_authelia_yml,
    ):
        """Orthanc only returns names: the route must join in the details."""
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.get("/modalities").respond(json=["SCANNER-1"])
            mock.get("/modalities/SCANNER-1/configuration").respond(
                json={"AET": "SCANNER1", "Host": "192.0.2.10", "Port": 104},
            )
            r = client.get("/api/admin/modalities")

        assert r.status_code == 200, r.text
        devices = r.json()["modalities"]
        assert len(devices) == 1
        assert devices[0] == {
            "name": "SCANNER-1", "aet": "SCANNER1",
            "host": "192.0.2.10", "port": 104,
        }

    def test_declaration(self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers):
        with respx.mock(base_url="http://orthanc:8042") as mock:
            route = mock.put("/modalities/IRM-1").respond(status_code=200, json={})
            r = client.put("/api/admin/modalities/IRM-1", json={
                "aet": "IRM1", "host": "192.0.2.20", "port": 104,
            }, headers=csrf_headers)

        assert r.status_code == 200, r.text
        assert route.called
        # The operation must leave a trace: declaring a device authorises a
        # third-party machine to drop studies here.
        entries_read = fake_redis.sync.xrange("admin:audit")
        assert any(e[1].get("event") == "orthanc.modality.saved" for e in entries_read)

    def test_ae_title_too_long_refused(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        """Sixteen characters at most: beyond that the device refuses the
        association without saying why, so better to say it up front."""
        r = client.put("/api/admin/modalities/TROP-LONG", json={
            "aet": "A" * 17, "host": "192.0.2.30", "port": 104,
        }, headers=csrf_headers)
        assert r.status_code == 422

    def test_port_out_of_bounds_refused(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        r = client.put("/api/admin/modalities/PORT-KO", json={
            "aet": "OK", "host": "192.0.2.30", "port": 70000,
        }, headers=csrf_headers)
        assert r.status_code == 422

    def test_deletion(self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers):
        with respx.mock(base_url="http://orthanc:8042") as mock:
            route = mock.delete("/modalities/IRM-1").respond(status_code=200, json={})
            r = client.delete("/api/admin/modalities/IRM-1", headers=csrf_headers)

        assert r.status_code == 200, r.text
        assert route.called

    def test_echo_reachable(self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers):
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.post("/modalities/IRM-1/echo").respond(status_code=200, json={})
            r = client.post("/api/admin/modalities/IRM-1/echo", headers=csrf_headers)

        assert r.status_code == 200, r.text
        assert r.json()["reachable"] is True

    def test_unreachable_echo_is_not_an_error(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        """A silent device is a result, not a failure: the route answers 200
        while reporting the failure, so the interface can show it."""
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.post("/modalities/IRM-1/echo").respond(
                status_code=500, text="TCP Initialization Error",
            )
            r = client.post("/api/admin/modalities/IRM-1/echo", headers=csrf_headers)

        assert r.status_code == 200, r.text
        assert r.json()["reachable"] is False
        assert "TCP" in r.json()["detail"]


class TestUserUpdate:
    """Modification d'un compte, et garde-fou anti-verrouillage.

    Without these routes, changing someone's group meant deleting their
    account and recreating it -- making them lose their password.
    """

    def test_partial_update(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        """Fields left out must not be overwritten."""
        r = client.patch("/api/admin/users/cuffel.gregory", json={
            "displayname": "Docteur Cuffel",
        }, headers=csrf_headers)
        assert r.status_code == 200, r.text

        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        record = yml["users"]["cuffel.gregory"]
        assert record["displayname"] == "Docteur Cuffel"
        assert record["email"] == "cuffel.gregory@gmail.com"   # inchange
        assert "admin" in record["groups"]                     # inchange

    def test_group_change(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        # A second administrator, without which the invariant would block.
        data = yaml.safe_load(tmp_paths["authelia"].read_text())
        data["users"]["autre.admin"] = dict(
            data["users"]["cuffel.gregory"], displayname="Autre", email="a@b.fr",
        )
        tmp_paths["authelia"].write_text(yaml.safe_dump(data))

        r = client.patch("/api/admin/users/cuffel.gregory", json={
            "groups": ["doctor"],
        }, headers=csrf_headers)
        assert r.status_code == 200, r.text

        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert yml["users"]["cuffel.gregory"]["groups"] == ["doctor"]

    def test_refuses_to_demote_the_last_admin(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        """Removing yourself from the admin group while being the only one
        would leave the stack with nobody to administer it. A 400 is expected
        -- not a 500, which does not tell a deliberate refusal from a
        failure."""
        r = client.patch("/api/admin/users/cuffel.gregory", json={
            "groups": ["doctor"],
        }, headers=csrf_headers)
        assert r.status_code == 400
        assert "admin" in r.text.lower()

    def test_refuses_to_disable_the_last_admin(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        r = client.patch("/api/admin/users/cuffel.gregory", json={
            "disabled": True,
        }, headers=csrf_headers)
        assert r.status_code == 400

    def test_unknown_user(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        r = client.patch("/api/admin/users/fantome", json={
            "displayname": "X",
        }, headers=csrf_headers)
        assert r.status_code == 404

    def test_no_field_provided(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        r = client.patch("/api/admin/users/cuffel.gregory", json={}, headers=csrf_headers)
        assert r.status_code == 400
