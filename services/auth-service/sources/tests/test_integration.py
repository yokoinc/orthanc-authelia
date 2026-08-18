"""
Integration tests: FastAPI endpoints + Redis + YAML/JSON files + httpx mocks.

Uses:
- TestClient (starlette) to call the endpoints
- fakeredis.aioredis to simulate Redis in memory
- respx to mock the calls to http://orthanc:8042/tools/reset
- tmp_path to isolate the authelia.yml + orthanc.json + backups files

Run:
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
    """Redirect the module-level paths to a per-test tmp_path."""
    authelia = tmp_path / "authelia.yml"
    orthanc = tmp_path / "orthanc.json"
    backups = tmp_path / "backups"
    settings = tmp_path / "backups" / "settings.json"
    monkeypatch.setattr(admin_module, "AUTHELIA_YML", authelia)
    monkeypatch.setattr(admin_module, "ORTHANC_JSON", orthanc)
    monkeypatch.setattr(admin_module, "BACKUPS_DIR", backups)
    monkeypatch.setattr(admin_module, "SETTINGS_FILE", settings)
    # The cache is module-level: without this reset a test would read what the
    # previous one wrote, in another tmp_path.
    admin_module._settings_cache["key"] = None
    admin_module._settings_cache["data"] = {}
    return {"authelia": authelia, "orthanc": orthanc, "backups": backups,
            "settings": settings}


@pytest.fixture
def fake_server():
    """Shared backend: the module's async client and the assertions' sync
    client hit the same datastore."""
    return fakeredis.FakeServer()


@pytest.fixture
def fake_redis(fake_server):
    """Inject a fake Redis into the module (synchronous for TestClient)."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True, server=fake_server)
    admin_module.set_redis(r)
    return r


@pytest.fixture
def redis_sync(fake_server):
    """Synchronous client to inspect Redis state in the assertions.

    We cannot use asyncio.run(fake_redis.get(...)): that would open a second
    event loop while the async connection is already bound to the TestClient's
    (RuntimeError: bound to a different event loop).
    """
    return fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)


@pytest.fixture
def admin_user():
    return admin_module.AdminUser(username="cuffel.gregory", groups=["admins"])


@pytest.fixture
def app(admin_user):
    """FastAPI app with the router + middlewares wired up."""
    app = FastAPI()
    app.include_router(admin_module.router)
    app.middleware("http")(admin_module.setup_gate)
    app.middleware("http")(admin_module.csrf_gate)
    # Dependency override: no real Authelia auth in tests
    app.dependency_overrides[admin_module.require_admin] = lambda: admin_user
    return app


@pytest.fixture
def client(app, tmp_paths, fake_redis):
    # Context manager is mandatory: otherwise TestClient opens one event loop
    # per request and the fakeredis connection, bound to the first, breaks on
    # the second.
    with TestClient(app) as c:
        yield c


@pytest.fixture
def csrf_headers(client):
    """Set up the double-submit cookie + header to get past csrf_gate."""
    client.cookies.set("orthanc_admin_csrf", "test-token")
    return {"x-csrf-token": "test-token"}


@pytest.fixture
def valid_orthanc_json(tmp_paths):
    """Pre-create a valid orthanc.json (with the critical DB flags)."""
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
def authelia_config(tmp_path, monkeypatch):
    """A commented Authelia configuration, as shipped in this repo."""
    path = tmp_path / "configuration.yml"
    path.write_text("""---
theme: auto

authentication_backend:
  file:
    path: /config/users_database.yml   # accounts
    watch: true

session:
  name: authelia_session           # Session cookie name
  expiration: 1h                   # Maximum session duration
  inactivity: 15m                  # Auto-logout after inactivity
  remember_me: 8h                  # "Remember me" duration
  redis:
    host: redis
    port: 6379

regulation:
  max_retries: 5
  ban_time: 5m                     # not a session duration
""")
    monkeypatch.setattr(admin_module, "AUTHELIA_CONFIG", path)
    return path


@pytest.fixture
def valid_authelia_yml(tmp_paths):
    """Pre-create a valid users_database.yml (1 active admin, argon2id)."""
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
# Test 1: setup wizard end-to-end
# ============================================================================

class TestSetupWizard:

    def test_full_flow(self, client, tmp_paths, fake_redis, redis_sync):
        """Empty Redis -> create admin -> finalize -> 2nd create blocked by middleware."""
        # Initial state: setup_completed absent
        # (fake_redis is fresh, no keys at all)

        # Step 1: create the first admin
        r = client.post("/auth/setup/create-admin", json={
            "username": "cuffel.gregory",
            "displayname": "Gregory Cuffel",
            "email": "cuffel.gregory@gmail.com",
            "password": "premier-admin-12345",
            "groups": ["admins"],
        })
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

        # The YAML must exist and hold the user with an argon2id hash
        assert tmp_paths["authelia"].exists()
        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert "cuffel.gregory" in yml["users"]
        assert yml["users"]["cuffel.gregory"]["password"].startswith("$argon2id$")
        assert "admins" in yml["users"]["cuffel.gregory"]["groups"]

        # Step 2: finalize
        r = client.post("/auth/setup/finalize")
        assert r.status_code == 200
        assert r.json()["admins"] == ["cuffel.gregory"]

        # Redis now carries the flag
        val = redis_sync.get("orthanc_authelia:setup_completed")
        assert val == "1"

        # Step 3: 2nd call blocked by setup_gate (redirect to /auth/admin)
        r = client.post("/auth/setup/create-admin", json={
            "username": "someone.else",
            "displayname": "Someone Else",
            "email": "someone@example.com",
            "password": "another-password-12345",
        }, follow_redirects=False)
        assert r.status_code == 404, "the wizard must be gone, not redirecting"

    def test_email_is_the_identity_when_no_login_given(
        self, client, tmp_paths, fake_redis,
    ):
        """No login field: the e-mail becomes the key in users_database.yml.

        Authelia matches accounts on that key, and this deployment uses e-mail
        addresses there. The former pattern forbade "@" outright, so the panel
        could not create an account in the very format the file already used.
        """
        r = client.post("/auth/setup/create-admin", json={
            "displayname": "Gregory Cuffel",
            "email": "cuffel.gregory@gmail.com",
            "password": "premier-admin-12345",
            "groups": ["admins"],
        })
        assert r.status_code == 200, r.text
        assert r.json()["username"] == "cuffel.gregory@gmail.com"

        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert "cuffel.gregory@gmail.com" in yml["users"]
        assert yml["users"]["cuffel.gregory@gmail.com"]["password"].startswith("$argon2id$")

    def test_explicit_login_still_accepted(self, client, tmp_paths, fake_redis):
        """A separate login remains possible for installs that use one."""
        r = client.post("/auth/setup/create-admin", json={
            "username": "cuffel.gregory",
            "displayname": "Gregory Cuffel",
            "email": "cuffel.gregory@gmail.com",
            "password": "premier-admin-12345",
        })
        assert r.status_code == 200, r.text
        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert "cuffel.gregory" in yml["users"]

    def test_finalize_refused_without_admin(self, client, tmp_paths, fake_redis):
        """Finalizing without an active admin = 400 (lockout invariant)."""
        # No POST create-admin beforehand
        r = client.post("/auth/setup/finalize")
        assert r.status_code == 400
        assert "admin" in r.text.lower()

    def test_create_admin_forces_admins_group(self, client, tmp_paths, fake_redis):
        """Even if the user forgets 'admins' in groups, we add it."""
        r = client.post("/auth/setup/create-admin", json={
            "username": "cuffel.gregory",
            "displayname": "Gregory",
            "email": "cuffel@example.com",
            "password": "long-password-1234",
            "groups": ["doctors"],  # NOT admins
        })
        assert r.status_code == 200
        yml = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert "admins" in yml["users"]["cuffel.gregory"]["groups"]


# ============================================================================
# Test 3: Orthanc config change + reload
# ============================================================================

class TestOrthancConfig:

    def test_patch_writes_file_and_calls_reset(
        self, client, tmp_paths, fake_redis, redis_sync, csrf_headers,
        valid_orthanc_json, monkeypatch,
    ):
        """PATCH -> JSON updated on disk + POST /tools/reset called + audit.

        Reset mode: only meaningful where Orthanc is started directly on the
        mounted file. The default is "restart", covered by its own test below.
        """
        monkeypatch.setattr(admin_module, "ORTHANC_APPLY_MODE", "reset")
        with respx.mock(base_url="http://orthanc:8042") as mock:
            reset_route = mock.post("/tools/reset").respond(status_code=200, json={})
            # The route now checks, after the reload, that Orthanc really runs
            # on this file: a 200 from /tools/reset proves it reloaded
            # something, not that it reloaded ours.
            mock.get("/system").respond(json={"Name": "New PACS Name"})

            r = client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "New PACS Name", "HttpCompressionEnabled": True},
            }, headers=csrf_headers)
            assert r.status_code == 200, r.text
            assert reset_route.called
            assert "warning" not in r.json(), "agreement: no warning expected"

        # The file has been updated
        new = json.loads(tmp_paths["orthanc"].read_text())
        assert new["Name"] == "New PACS Name"
        assert new["HttpCompressionEnabled"] is True
        # Critical flags preserved
        assert new["DicomModalitiesInDatabase"] is True
        assert new["OrthancPeersInDatabase"] is True

        # A backup has been created
        backups = list(tmp_paths["backups"].glob("orthanc.json.bak.*"))
        assert len(backups) == 1

        # The audit stream has an entry
        entries = redis_sync.xrange("admin:audit")
        assert len(entries) >= 1
        _, fields = entries[-1]
        assert fields["event"] == "orthanc.config.updated"
        assert fields["actor"] == "cuffel.gregory"

    def test_reset_that_reloads_another_file_is_reported(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
        monkeypatch,
    ):
        """A 200 from /tools/reset proves Orthanc reloaded something, not that
        it reloaded ours.

        The orthancteam image merges /etc/orthanc/*.json with the ORTHANC__*
        variables into a copy at startup, and that copy is what a reload
        re-reads. Announcing plain success there sends the operator hunting
        for the fault everywhere except where it is.
        """
        monkeypatch.setattr(admin_module, "ORTHANC_APPLY_MODE", "reset")
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.post("/tools/reset").respond(status_code=200, json={})
            # Orthanc reports another name: it is not reading our file.
            mock.get("/system").respond(json={"Name": "Nom Impose Par Le Compose"})

            r = client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "Nom Ecrit Dans Le Fichier"},
            }, headers=csrf_headers)

        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert "warning" in r.json(), "the divergence must be reported"
        assert "restart" in r.json()["warning"].lower()


    def test_default_mode_writes_without_calling_reset(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """Default mode writes the file and reports it, without POSTing a reset.

        On orthancteam images Orthanc runs on a copy built at container start,
        so a reset answers 200 while changing nothing -- reporting a success
        that never happened. Better to write and say a restart is needed.
        """
        assert admin_module.ORTHANC_APPLY_MODE == "restart"

        # assert_all_called=False: the route is declared precisely so we can
        # prove it stays untouched.
        with respx.mock(base_url="http://orthanc:8042", assert_all_called=False) as mock:
            reset_route = mock.post("/tools/reset").respond(status_code=200, json={})

            r = client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "New Name"},
            }, headers=csrf_headers)

            assert r.status_code == 200, r.text
            assert not reset_route.called, "no reset must be sent in restart mode"

        body = r.json()
        assert body["applied"] is False
        assert body["restart_required"] is True
        assert "restart" in body["detail"].lower()

        # The file itself is written all the same
        assert json.loads(tmp_paths["orthanc"].read_text())["Name"] == "New Name"

    def test_patch_refuses_non_whitelisted_path(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """A path outside the whitelist returns 400."""
        r = client.patch("/api/admin/orthanc/config", json={
            "changes": {"PostgreSQL.Password": "hack"},
        }, headers=csrf_headers)
        assert r.status_code == 400
        assert "not editable" in r.text.lower()

    def test_patch_refuses_disabling_critical_flag(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """Disabling DicomModalitiesInDatabase = 400."""
        r = client.patch("/api/admin/orthanc/config", json={
            "changes": {"DicomModalitiesInDatabase": False},
        }, headers=csrf_headers)
        assert r.status_code == 400


# ============================================================================
# Test 4: rollback through /api/admin/backups/restore
# ============================================================================

class TestBackupRestore:

    def test_orthanc_rollback(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_orthanc_json,
    ):
        """PATCH then restore = file put back to its initial state.

        assert_all_called=False: in the default mode nothing posts a reset, the
        route is only declared so the test stays off the network.
        """
        with respx.mock(base_url="http://orthanc:8042", assert_all_called=False) as mock:
            mock.post("/tools/reset").respond(status_code=200, json={})

            # Change it
            client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "Modified"},
            }, headers=csrf_headers)
            assert json.loads(tmp_paths["orthanc"].read_text())["Name"] == "Modified"

            # Fetch the name of the backup that was created
            backups = sorted(tmp_paths["backups"].glob("orthanc.json.bak.*"))
            assert backups
            backup_name = backups[0].name

            # Restore
            r = client.post(
                f"/api/admin/backups/restore?backup_name={backup_name}",
                headers=csrf_headers,
            )
            assert r.status_code == 200, r.text

        # The file is back to its initial Name
        restored = json.loads(tmp_paths["orthanc"].read_text())
        assert restored["Name"] == valid_orthanc_json["Name"]

    def test_list_backups_shows_accounts(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_authelia_yml,
    ):
        """The listing names the accounts each backup holds.

        Backups were written on every change but nothing exposed them, so from
        the panel they might as well not have existed. And picking the right one
        hinges on what it contains, not on its file name.
        """
        admin_module._backup(tmp_paths["authelia"])

        r = client.get("/api/admin/backups")
        assert r.status_code == 200, r.text
        backups = r.json()["backups"]
        assert len(backups) == 1
        assert backups[0]["target"] == tmp_paths["authelia"].name
        assert "1 compte(s)" in backups[0]["detail"]
        assert "cuffel.gregory" in backups[0]["detail"]

    def test_list_backups_ignores_unrestorable_files(
        self, client, tmp_paths, fake_redis, valid_authelia_yml,
    ):
        """Only what restore can actually put back is offered."""
        admin_module._backup(tmp_paths["authelia"])
        (tmp_paths["backups"] / "notes.txt").write_text("not a backup")
        (tmp_paths["backups"] / "users_database.yml.preflight").write_text("manual copy")

        names = [b["name"] for b in client.get("/api/admin/backups").json()["backups"]]
        assert all(".bak." in n for n in names)
        assert "notes.txt" not in names

    def test_restore_users_database_puts_the_accounts_back(
        self, client, tmp_paths, fake_redis, csrf_headers, valid_authelia_yml,
    ):
        """Restore returns the file to the backed-up state, keeping the inode."""
        backup = admin_module._backup(tmp_paths["authelia"])
        before = tmp_paths["authelia"].read_text()
        inode_before = tmp_paths["authelia"].stat().st_ino

        # Wipe an account the way a mistaken deletion would
        tmp_paths["authelia"].write_text("users: {}\n")

        r = client.post(
            f"/api/admin/backups/restore?backup_name={backup.name}",
            headers=csrf_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["target"] == tmp_paths["authelia"].name
        assert tmp_paths["authelia"].read_text() == before
        # Same inode: the other containers see the restored file through their mount
        assert tmp_paths["authelia"].stat().st_ino == inode_before

    def test_restore_rejects_bad_name(self, client, tmp_paths, fake_redis, csrf_headers):
        """A name without .bak. in it = 404."""
        r = client.post(
            "/api/admin/backups/restore?backup_name=evil_traversal",
            headers=csrf_headers,
        )
        assert r.status_code == 404


# ============================================================================
# Test 5: CSRF rejection
# ============================================================================

class TestCSRF:

    def test_post_without_token_refused(self, client, tmp_paths, fake_redis):
        """POST /api/admin/* without the CSRF cookie + header = 403."""
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
        """GET is never subject to CSRF (idempotent)."""
        # GET /api/admin/cf-access without a cookie
        r = client.get("/api/admin/cf-access")
        assert r.status_code == 200  # OK, csrf_gate laisse passer

    def test_internal_verify_bypass_csrf(self, client, fake_redis):
        """/api/internal/* is not /api/admin/* and bypasses the gate."""
        r = client.get("/api/internal/verify-cf", headers={
            "x-cf-client-id": "x", "x-cf-client-secret": "y",
        })
        # 503 (not configured) proves we reached the endpoint, not a 403 CSRF
        assert r.status_code == 503


# ============================================================================
# Test 6: file lock — concurrent orthanc.json writes
# ============================================================================

class TestFileLock:

    def test_concurrent_write_returns_423(
        self, client, tmp_paths, fake_redis, csrf_headers,
        valid_orthanc_json, monkeypatch,
    ):
        """
        An outside thread holds the lock, the API request waits then times out -> 423.
        Lowers the admin_module timeout to 1s so the test stays fast.
        """
        # Patch the FileLock timeout to keep this quick
        orig_flock = admin_module.FileLock

        def fast_flock(path, timeout=None):
            return orig_flock(path, timeout=1)  # 1s instead of 5s

        monkeypatch.setattr(admin_module, "FileLock", fast_flock)

        lock_path = str(tmp_paths["orthanc"]) + ".lock"
        barrier = threading.Barrier(2)

        def hold_lock():
            with orig_flock(lock_path, timeout=5):
                barrier.wait()  # tell the test the lock is held
                time.sleep(3)   # hold it longer than the endpoint's timeout

        holder = threading.Thread(target=hold_lock)
        holder.start()
        try:
            barrier.wait()  # wait until hold_lock owns the lock

            # Now try to write through the API
            r = client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "should not succeed"},
            }, headers=csrf_headers)
            assert r.status_code == 423
            assert "locked" in r.text.lower()
        finally:
            holder.join()

        # The file was NOT modified (the lock prevented the write)
        content = json.loads(tmp_paths["orthanc"].read_text())
        assert content["Name"] == valid_orthanc_json["Name"]


# ============================================================================
# Test 7: Orthanc auto-rollback when /tools/reset fails
# ============================================================================

class TestAutoRollback:

    def test_rollback_on_reset_failure(
        self, client, tmp_paths, fake_redis, redis_sync, csrf_headers,
        valid_orthanc_json, monkeypatch,
    ):
        """PATCH -> /tools/reset returns 500 -> auto-rollback -> 502 but file restored."""
        monkeypatch.setattr(admin_module, "ORTHANC_APPLY_MODE", "reset")
        # 1st reset (the failing one) then 2nd (the rollback one, succeeding)
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

        # The file is back to its initial Name (rollback done)
        current = json.loads(tmp_paths["orthanc"].read_text())
        assert current["Name"] == valid_orthanc_json["Name"]

        # The audit trail shows the rollback
        entries = redis_sync.xrange("admin:audit")
        events = [f["event"] for _, f in entries]
        assert "orthanc.config.rolled_back" in events


# ============================================================================
# Test 8: setup wizard locked after the first create-admin
# ============================================================================

class TestSetupLockout:

    def test_existing_install_without_flag_closes_the_wizard(
        self, client, tmp_paths, fake_redis, redis_sync, valid_authelia_yml,
    ):
        """Stack already in service, Redis flag absent: the wizard stays shut.

        This is the upgrade case: the setup_completed key does not exist in the
        Redis of an install predating the panel. If the wizard reopened, it sits
        outside SSO -- anyone could create an admin account on a production PACS.
        """
        assert redis_sync.get("orthanc_authelia:setup_completed") is None

        r = client.get("/auth/setup", follow_redirects=False)
        assert r.status_code == 404

        # The flag is frozen on the way, no need to re-read the YAML afterwards
        assert redis_sync.get("orthanc_authelia:setup_completed") == "1"

    def test_fresh_install_keeps_the_wizard_open(
        self, client, tmp_paths, fake_redis, redis_sync,
    ):
        """No users_database.yml: genuine first install, wizard open."""
        assert not tmp_paths["authelia"].exists()

        r = client.get("/auth/setup", follow_redirects=False)
        assert r.status_code == 200
        assert redis_sync.get("orthanc_authelia:setup_completed") is None

    def test_corrupt_yaml_closes_the_wizard(
        self, client, tmp_paths, fake_redis, redis_sync,
    ):
        """YAML present but broken: fail-closed, we do not reopen the wizard."""
        tmp_paths["authelia"].write_text("users:\n  x: {not: valid: yaml")

        r = client.get("/auth/setup", follow_redirects=False)
        assert r.status_code == 404

    def test_admin_group_name_is_configurable(
        self, client, tmp_paths, fake_redis, redis_sync, monkeypatch,
    ):
        """The admin group name follows ADMIN_GROUP, not a hardcoded "admins".

        Existing installs do not all use the same name: this stack uses "admin"
        in the singular. If the module stays on "admins", no admin is
        recognised -- the wizard reopens and the panel answers 403 to everyone.
        """
        monkeypatch.setattr(admin_module, "ADMIN_GROUP", "admin")
        tmp_paths["authelia"].write_text(yaml.safe_dump({
            "users": {
                "boss": {
                    "disabled": False,
                    "displayname": "Boss",
                    "email": "boss@example.com",
                    "password": admin_module._hasher.hash("un-mot-de-passe-1234"),
                    "groups": ["admin"],
                },
            },
        }))

        r = client.get("/auth/setup", follow_redirects=False)
        assert r.status_code == 404, "an 'admin' group must close the wizard"
        assert redis_sync.get("orthanc_authelia:setup_completed") == "1"

    def test_second_create_admin_refused(self, client, tmp_paths, fake_redis, redis_sync):
        """After one create-admin, a 2nd call = 409 until finalize."""
        r1 = client.post("/auth/setup/create-admin", json={
            "username": "first.admin",
            "displayname": "First",
            "email": "first@example.com",
            "password": "premier-admin-1234",
        })
        assert r1.status_code == 200

        r2 = client.post("/auth/setup/create-admin", json={
            "username": "second.admin",
            "displayname": "Second",
            "email": "second@example.com",
            "password": "second-admin-12345",
        })
        assert r2.status_code == 409
        assert "already been created" in r2.text.lower()

    def test_finalize_clears_lock_next_setup_impossible_anyway(
        self, client, tmp_paths, fake_redis, redis_sync,
    ):
        """After finalize the first_admin lock is removed (but setup_gate shuts everything)."""
        client.post("/auth/setup/create-admin", json={
            "username": "admin.one",
            "displayname": "Admin",
            "email": "a@b.com",
            "password": "admin-password-1234",
        })
        client.post("/auth/setup/finalize")

        first_admin_flag = redis_sync.get("orthanc_authelia:setup_first_admin_created")
        setup_flag = redis_sync.get("orthanc_authelia:setup_completed")
        assert first_admin_flag is None
        assert setup_flag == "1"


# ============================================================================
# Test 9: Redis down does not stand between a valid assertion and an upload
# ============================================================================

class TestRedisResilience:

    def test_verify_cf_survives_a_redis_blackout(self, app, tmp_paths, monkeypatch):
        """A Redis outage no longer blocks uploads.

        Verification used to compare a pair held in Redis, so losing Redis meant
        refusing every upload. It now rests on Cloudflare's signature, and Redis
        is only touched for a hit counter -- whose failure is swallowed on
        purpose. Losing Redis costs a metric, not the ingestion path.
        """
        from redis.exceptions import RedisError

        class BrokenRedis:
            async def get(self, k):
                raise RedisError("simulated blackout")

            async def incr(self, k):
                raise RedisError("simulated blackout")

        admin_module.set_redis(BrokenRedis())

        team = TestCFAccessJWT.TEAM
        monkeypatch.setattr(admin_module, "CF_ACCESS_TEAM_DOMAIN", team)
        monkeypatch.setattr(admin_module, "CF_ACCESS_AUD", TestCFAccessJWT.AUD)
        monkeypatch.setattr(admin_module, "_jwks_cache", {"keys": {}, "fetched_at": 0.0})

        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwks = TestCFAccessJWT.jwks_for(key)
        token = TestCFAccessJWT().make_token(key)

        with TestClient(app) as c, respx.mock:
            respx.get(f"https://{team}/cdn-cgi/access/certs").mock(
                return_value=httpx.Response(200, json=jwks))
            r = c.get("/api/internal/verify-cf",
                      headers={"cf-access-jwt-assertion": token})
        assert r.status_code == 204


# ============================================================================
# Test 10: corrupt YAML/JSON -> readable 500 hinting at restore
# ============================================================================

class TestCorruptConfig:

    def test_corrupt_authelia_yml_returns_readable_500(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        """Syntactically broken YAML -> 500 with a message hinting at restore."""
        tmp_paths["authelia"].write_text("users:\n  cuffel: {this is: not: valid: yaml")

        r = client.get("/api/admin/users")
        assert r.status_code == 500
        assert "corrupt" in r.text.lower()
        assert "backups" in r.text.lower()

    def test_corrupt_orthanc_json_returns_readable_500(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        """Syntactically broken JSON -> 500 with a restore message."""
        tmp_paths["orthanc"].write_text('{"Name": "unclosed')

        r = client.get("/api/admin/orthanc/config")
        assert r.status_code == 500
        assert "corrupt" in r.text.lower()
        assert "backups" in r.text.lower()


# ============================================================================
# Test 11: health endpoint
# ============================================================================

class TestHealth:

    def test_health_reports_component_status(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, valid_orthanc_json,
    ):
        """/api/admin/health returns the state of every component."""
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

    def test_setup_page_renders_when_setup_not_done(
        self, client, tmp_paths, fake_redis,
    ):
        """GET /auth/setup before finalize = HTML with the form."""
        # ADMIN_TEMPLATES_DIR is set by the test runner. If absent, skip.
        if not (admin_module.TEMPLATES_DIR / "setup.html").exists():
            import pytest
            pytest.skip("templates/setup.html absent dans le layout de test")

        r = client.get("/auth/setup")
        assert r.status_code == 200
        assert "setup-form" in r.text
        assert "create-admin" in r.text  # le fetch JS pointe dessus

    def test_setup_page_redirects_when_setup_done(
        self, client, tmp_paths, fake_redis, redis_sync,
    ):
        """GET /auth/setup after finalize = 302 to /auth/admin (setup_gate)."""
        redis_sync.set("orthanc_authelia:setup_completed", "1")

        r = client.get("/auth/setup", follow_redirects=False)
        assert r.status_code == 404

    def test_admin_page_sets_csrf_cookie(
        self, client, tmp_paths, fake_redis, redis_sync, valid_authelia_yml,
    ):
        """GET /auth/admin after setup = HTML + orthanc_admin_csrf cookie set."""
        if not (admin_module.TEMPLATES_DIR / "admin.html").exists():
            import pytest
            pytest.skip("templates/admin.html absent dans le layout de test")

        redis_sync.set("orthanc_authelia:setup_completed", "1")

        r = client.get("/auth/admin")
        assert r.status_code == 200
        # CSRF cookie set
        assert "orthanc_admin_csrf" in r.cookies
        assert len(r.cookies["orthanc_admin_csrf"]) >= 40

    def test_health_reports_corrupt_orthanc_json(
        self, client, tmp_paths, fake_redis, valid_authelia_yml,
    ):
        """Corrupt orthanc.json = health reports KO on that component."""
        tmp_paths["orthanc"].write_text('{"unclosed')

        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.get("/system").respond(status_code=200, json={})

            r = client.get("/api/admin/health")
            assert r.status_code == 200
            assert r.json()["checks"]["orthanc_json"]["ok"] is False


# ============================================================================
# Test 12: commented orthanc.json (JSONC)
# ============================================================================

class TestJsoncConfig:
    """Orthanc accepts comments in its config, json.loads does not.

    This repo's orthanc.json carries 128 of them. Re-serialising the whole file
    through json.dumps would produce a valid file but would strip the
    administrator's configuration of all its documentation.
    """

    SAMPLE = """{
  // =========================================================
  // CONFIGURATION ORTHANC
  // =========================================================
  "Name": "Ancien Nom",          // nom affiche dans l'UI
  "DicomAet": "OLDAET",
  /* bloc de commentaire
     sur plusieurs lignes */
  "DicomModalitiesInDatabase": true,
  "OrthancPeersInDatabase": true,
  "HttpPort": 8042,
  "NotAComment": "http://example.com/a//b",
  "DicomWeb": {
    // sous-section
    "Enable": true,
    "Root": "/dicom-web/"
  }
}
"""

    def test_reads_config_with_comments(self, tmp_paths):
        tmp_paths["orthanc"].write_text(self.SAMPLE)
        config = admin_module._load_orthanc_config()
        assert config["Name"] == "Ancien Nom"
        assert config["DicomWeb"]["Root"] == "/dicom-web/"
        # A // inside a string is not a comment
        assert config["NotAComment"] == "http://example.com/a//b"

    def test_patch_preserves_comments(self, tmp_paths):
        out = admin_module._patch_jsonc(self.SAMPLE, {
            "Name": "Nouveau Nom",
            "DicomWeb.Root": "/dw/",
        })

        assert "CONFIGURATION ORTHANC" in out
        assert "// nom affiche dans l'UI" in out
        assert "bloc de commentaire" in out
        assert "// sous-section" in out

        config = json.loads(admin_module._mask_jsonc_comments(out))
        assert config["Name"] == "Nouveau Nom"
        assert config["DicomWeb"]["Root"] == "/dw/"
        assert config["DicomAet"] == "OLDAET"          # untouched
        assert config["NotAComment"] == "http://example.com/a//b"

    def test_patch_inserts_missing_key(self, tmp_paths):
        out = admin_module._patch_jsonc(self.SAMPLE, {"StableAge": 30})
        config = json.loads(admin_module._mask_jsonc_comments(out))
        assert config["StableAge"] == 30
        assert config["Name"] == "Ancien Nom"
        assert "CONFIGURATION ORTHANC" in out

    def test_patch_endpoint_keeps_comments_end_to_end(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        tmp_paths["orthanc"].write_text(self.SAMPLE)
        with respx.mock:
            respx.post("http://orthanc:8042/tools/reset").mock(
                return_value=httpx.Response(200, json={}),
            )
            r = client.patch(
                "/api/admin/orthanc/config",
                json={"changes": {"Name": "Nom De Test", "HttpPort": 8043}},
                headers=csrf_headers,
            )
        assert r.status_code == 200, r.text

        written = tmp_paths["orthanc"].read_text()
        assert "CONFIGURATION ORTHANC" in written
        assert "// nom affiche dans l'UI" in written
        config = json.loads(admin_module._mask_jsonc_comments(written))
        assert config["Name"] == "Nom De Test"
        assert config["HttpPort"] == 8043


# ============================================================================
# Test 13: Authelia session durations
# ============================================================================

class TestSessionDurations:

    def test_read_returns_current_durations(
        self, client, tmp_paths, fake_redis, authelia_config,
    ):
        r = client.get("/api/admin/session")
        assert r.status_code == 200, r.text
        assert r.json()["durations"] == {
            "expiration": "1h", "inactivity": "15m", "remember_me": "8h",
        }

    def test_patch_keeps_comments_and_other_sections(
        self, client, tmp_paths, fake_redis, csrf_headers, authelia_config,
    ):
        """Only the targeted values change: comments and layout survive."""
        r = client.patch("/api/admin/session", json={"inactivity": "45m"},
                         headers=csrf_headers)
        assert r.status_code == 200, r.text
        assert r.json()["restart_required"] is True

        written = authelia_config.read_text()
        assert "# Auto-logout after inactivity" in written
        assert "# Session cookie name" in written
        assert "inactivity: 45m" in written
        # A same-named key in another section is left alone
        assert "ban_time: 5m" in written

        config = yaml.safe_load(written)
        assert config["session"]["inactivity"] == "45m"
        assert config["session"]["expiration"] == "1h"
        assert config["regulation"]["max_retries"] == 5

    def test_patch_refuses_a_malformed_duration(
        self, client, tmp_paths, fake_redis, csrf_headers, authelia_config,
    ):
        before = authelia_config.read_text()
        r = client.patch("/api/admin/session", json={"inactivity": "45 minutes"},
                         headers=csrf_headers)
        assert r.status_code == 400
        assert "duration" in r.text.lower()
        assert authelia_config.read_text() == before, "nothing must be written"

    def test_patch_backs_up_before_writing(
        self, client, tmp_paths, fake_redis, csrf_headers, authelia_config,
    ):
        before = authelia_config.read_text()
        r = client.patch("/api/admin/session", json={"expiration": "2h"},
                         headers=csrf_headers)
        assert r.status_code == 200, r.text

        backup = tmp_paths["backups"] / r.json()["backup"]
        assert backup.exists()
        assert backup.read_text() == before


# ============================================================================
# Test 14: Cloudflare Access assertion (JWT)
# ============================================================================

class TestCFAccessStatus:

    def test_status_reports_the_verification_state(
        self, client, fake_redis, monkeypatch,
    ):
        """The tab reports, it no longer offers a pair to store.

        Cloudflare rotates its own tokens and relays a signed assertion; there
        is nothing on this side to keep in step, so the endpoint exposes what is
        pinned and whether nginx enforces it.
        """
        monkeypatch.setattr(admin_module, "CF_ACCESS_TEAM_DOMAIN", "team.cloudflareaccess.com")
        monkeypatch.setattr(admin_module, "CF_ACCESS_AUD", "a" * 40)
        monkeypatch.setattr(admin_module, "CF_ACCESS_ENFORCED", True)

        body = client.get("/api/admin/cf-access").json()
        assert body["team_domain"] == "team.cloudflareaccess.com"
        assert body["configured"] is True
        assert body["enforced"] is True
        assert "…" in body["aud_masked"], "the audience is shortened, not dumped"

    def test_status_flags_an_unconfigured_verification(self, client, fake_redis, monkeypatch):
        monkeypatch.setattr(admin_module, "CF_ACCESS_TEAM_DOMAIN", "")
        body = client.get("/api/admin/cf-access").json()
        assert body["configured"] is False

    def test_rotate_endpoint_is_gone(self, client, fake_redis, csrf_headers):
        """Storing a pair could never have gated anything: it is not offered."""
        r = client.post("/api/admin/cf-access/rotate", json={
            "client_id": "id-one-abc.access", "client_secret": "1" * 64,
        }, headers=csrf_headers)
        assert r.status_code in (404, 405)


class TestCFAccessJWT:
    """Cloudflare consumes the service token at its edge and relays a signed
    assertion instead -- measured on this stack: cf_id=no, cf_secret=no,
    cf_jwt=yes. So the origin verifies that assertion, and both issuer and
    audience are pinned: a token signed by any other Cloudflare team would
    verify perfectly well against that team's own keys.
    """

    TEAM = "example.cloudflareaccess.com"
    AUD = "3a423c8b4e8dcf314759c36218857a0a"
    KID = "test-key-1"

    @pytest.fixture
    def signing_key(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @staticmethod
    def jwks_for(signing_key):
        """The team's published key set, as Cloudflare would serve it."""
        import json as _json
        import jwt
        jwk = _json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
        jwk.update({"kid": TestCFAccessJWT.KID, "alg": "RS256", "use": "sig"})
        return {"keys": [jwk]}

    @pytest.fixture
    def cf_configured(self, monkeypatch, signing_key):
        """Pin the team and audience, and publish the matching JWKS."""
        monkeypatch.setattr(admin_module, "CF_ACCESS_TEAM_DOMAIN", self.TEAM)
        monkeypatch.setattr(admin_module, "CF_ACCESS_AUD", self.AUD)
        # The cache is module-level: a stale entry would leak between tests.
        monkeypatch.setattr(admin_module, "_jwks_cache", {"keys": {}, "fetched_at": 0.0})
        return self.jwks_for(signing_key)

    def make_token(self, signing_key, **overrides):
        import jwt
        claims = {
            "aud": self.AUD,
            "iss": f"https://{self.TEAM}",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
            "email": "uploader@example.com",
        }
        claims.update(overrides)
        return jwt.encode(claims, signing_key, algorithm="RS256",
                          headers={"kid": overrides.pop("kid", self.KID)})

    def _call(self, client, token):
        return client.get("/api/internal/verify-cf",
                          headers={"cf-access-jwt-assertion": token})

    def test_valid_assertion_accepted(self, client, fake_redis, signing_key, cf_configured):
        with respx.mock:
            respx.get(f"https://{self.TEAM}/cdn-cgi/access/certs").mock(
                return_value=httpx.Response(200, json=cf_configured))
            r = self._call(client, self.make_token(signing_key))
        assert r.status_code == 204

    def test_token_from_another_team_refused(self, client, fake_redis, signing_key, cf_configured):
        """The whole point of pinning the issuer."""
        with respx.mock:
            respx.get(f"https://{self.TEAM}/cdn-cgi/access/certs").mock(
                return_value=httpx.Response(200, json=cf_configured))
            token = self.make_token(signing_key, iss="https://attacker.cloudflareaccess.com")
            r = self._call(client, token)
        assert r.status_code == 403

    def test_token_for_another_application_refused(self, client, fake_redis, signing_key, cf_configured):
        with respx.mock:
            respx.get(f"https://{self.TEAM}/cdn-cgi/access/certs").mock(
                return_value=httpx.Response(200, json=cf_configured))
            r = self._call(client, self.make_token(signing_key, aud="some-other-app"))
        assert r.status_code == 403

    def test_expired_assertion_refused(self, client, fake_redis, signing_key, cf_configured):
        with respx.mock:
            respx.get(f"https://{self.TEAM}/cdn-cgi/access/certs").mock(
                return_value=httpx.Response(200, json=cf_configured))
            r = self._call(client, self.make_token(signing_key, exp=int(time.time()) - 10))
        assert r.status_code == 403

    def test_assertion_signed_by_an_unpublished_key_refused(self, client, fake_redis, cf_configured):
        """A correctly shaped token whose key is not in the JWKS."""
        from cryptography.hazmat.primitives.asymmetric import rsa
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with respx.mock:
            respx.get(f"https://{self.TEAM}/cdn-cgi/access/certs").mock(
                return_value=httpx.Response(200, json=cf_configured))
            r = self._call(client, self.make_token(other))
        assert r.status_code == 403

    def test_missing_assertion_refused(self, client, fake_redis, cf_configured):
        r = client.get("/api/internal/verify-cf")
        assert r.status_code == 403

    def test_unreachable_jwks_fails_closed(self, client, fake_redis, signing_key, cf_configured):
        with respx.mock:
            respx.get(f"https://{self.TEAM}/cdn-cgi/access/certs").mock(
                side_effect=httpx.ConnectError("down"))
            r = self._call(client, self.make_token(signing_key))
        assert r.status_code == 403

    def test_not_configured_returns_503(self, client, fake_redis, monkeypatch):
        """nginx must never reach this: the auth_request line and the team
        domain are enabled together."""
        monkeypatch.setattr(admin_module, "CF_ACCESS_TEAM_DOMAIN", "")
        r = client.get("/api/internal/verify-cf",
                       headers={"cf-access-jwt-assertion": "whatever"})
        assert r.status_code == 503


class TestModalities:
    """DICOM devices: declaration, removal, connectivity test.

    These routes go through Orthanc's API, simulated here. Without them the
    equipment page would only be covered by the end-to-end script, run by
    hand: CI only executes the unit tests, so a change breaking them would
    go through green.
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

    def test_declaration(self, client, tmp_paths, fake_redis, valid_authelia_yml,
                         csrf_headers, redis_sync):
        with respx.mock(base_url="http://orthanc:8042") as mock:
            route = mock.put("/modalities/IRM-1").respond(status_code=200, json={})
            r = client.put("/api/admin/modalities/IRM-1", json={
                "aet": "IRM1", "host": "192.0.2.20", "port": 104,
            }, headers=csrf_headers)

        assert r.status_code == 200, r.text
        assert route.called
        # The operation must leave a trace: declaring a device authorises a
        # third-party machine to drop studies here.
        entries = redis_sync.xrange("admin:audit")
        assert any(e[1].get("event") == "orthanc.modality.saved" for e in entries)

    def test_ae_title_too_long_refused(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        """Sixteen characters at most: beyond that the device refuses the
        association without saying why, so better to say it up front."""
        r = client.put("/api/admin/modalities/TOO-LONG", json={
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

    def test_deletion(self, client, tmp_paths, fake_redis, valid_authelia_yml,
                      csrf_headers):
        with respx.mock(base_url="http://orthanc:8042") as mock:
            route = mock.delete("/modalities/IRM-1").respond(status_code=200, json={})
            r = client.delete("/api/admin/modalities/IRM-1", headers=csrf_headers)

        assert r.status_code == 200, r.text
        assert route.called

    def test_echo_reachable(self, client, tmp_paths, fake_redis, valid_authelia_yml,
                            csrf_headers):
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.post("/modalities/IRM-1/echo").respond(status_code=200, json={})
            r = client.post("/api/admin/modalities/IRM-1/echo", headers=csrf_headers)

        assert r.status_code == 200, r.text
        assert r.json()["reachable"] is True

    def test_unreachable_echo_is_not_an_error(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        """A silent device is a result, not a failure: the route answers 200
        while reporting it, so the interface can show it."""
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.post("/modalities/MUET/echo").respond(status_code=500, text="timeout")
            r = client.post("/api/admin/modalities/MUET/echo", headers=csrf_headers)

        assert r.status_code == 200, r.text
        assert r.json()["reachable"] is False
        assert "timeout" in r.json()["detail"]


class TestCFAccessSettings:
    """What the origin pins, settable from the panel.

    These three values used to be readable only from the environment, fixed at
    import: changing one meant editing the compose file and recreating the
    container -- for a setting the panel ought to offer. They now live in the
    settings file and are resolved per request.
    """

    def test_saved_then_in_force(self, client, tmp_paths, fake_redis,
                                 valid_authelia_yml, csrf_headers):
        """The next GET must report what was just written, with no restart."""
        r = client.put("/api/admin/cf-access", json={
            "team_domain": "equipe.cloudflareaccess.com",
            "aud": "a" * 64,
            "enforced": True,
        }, headers=csrf_headers)
        assert r.status_code == 200, r.text

        state = client.get("/api/admin/cf-access").json()
        assert state["team_domain"] == "equipe.cloudflareaccess.com"
        assert state["enforced"] is True
        assert state["configured"] is True

    def test_survives_a_fresh_read(self, client, tmp_paths, fake_redis,
                                   valid_authelia_yml, csrf_headers):
        """Written to disk, not just held in the cache."""
        client.put("/api/admin/cf-access", json={
            "team_domain": "equipe.cloudflareaccess.com",
            "aud": "b" * 64, "enforced": False,
        }, headers=csrf_headers)

        admin_module._settings_cache["key"] = None
        assert tmp_paths["settings"].exists()
        assert admin_module._cf_team_domain() == "equipe.cloudflareaccess.com"

    def test_url_form_accepted(self, client, tmp_paths, fake_redis,
                               valid_authelia_yml, csrf_headers):
        """Pasted from the dashboard, the domain carries its scheme. The
        issuer is rebuilt as https://<domain>, so keeping it would yield
        https://https://... and every verification would fail."""
        client.put("/api/admin/cf-access", json={
            "team_domain": "https://equipe.cloudflareaccess.com/",
            "aud": "c" * 64, "enforced": False,
        }, headers=csrf_headers)
        assert admin_module._cf_team_domain() == "equipe.cloudflareaccess.com"

    def test_enforcing_without_the_pair_refused(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, csrf_headers,
    ):
        """Ticking the box with nothing pinned would answer 503 on every
        upload. Better a refusal than an endpoint locked by a checkbox."""
        r = client.put("/api/admin/cf-access", json={
            "team_domain": "", "aud": "", "enforced": True,
        }, headers=csrf_headers)
        assert r.status_code == 400

    def test_environment_still_wins_until_first_write(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, monkeypatch,
    ):
        """An install that never opens the panel keeps behaving as before."""
        monkeypatch.setattr(admin_module, "CF_ACCESS_TEAM_DOMAIN", "depuis-env.com")
        assert admin_module._cf_team_domain() == "depuis-env.com"

    def test_unreadable_settings_degrade_to_defaults(
        self, client, tmp_paths, fake_redis, valid_authelia_yml, monkeypatch,
    ):
        """A broken settings file must not take the service down with it."""
        tmp_paths["settings"].parent.mkdir(parents=True, exist_ok=True)
        tmp_paths["settings"].write_text("{ not json", encoding="utf-8")
        admin_module._settings_cache["key"] = None
        monkeypatch.setattr(admin_module, "CF_ACCESS_TEAM_DOMAIN", "repli.com")
        assert admin_module._cf_team_domain() == "repli.com"


class TestLastAdminProtected:
    """The last active administrator cannot be deleted.

    The invariant existed, but only as a side effect of _validate_authelia
    during the write: the operator got a bare 500 and no reason. The account
    did survive -- the write aborts before persisting -- yet a safeguard that
    reports itself as a server error is not one.
    """

    def test_deleting_the_only_admin_is_refused(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        hasher = admin_module._hasher
        data = {"users": {
            "admin.principal": {
                "disabled": False, "displayname": "Admin",
                "email": "admin@example.com",
                "password": hasher.hash("un-mot-de-passe-12345"),
                "groups": ["admins"],
            },
        }}
        tmp_paths["authelia"].write_text(yaml.safe_dump(data))

        r = client.delete("/api/admin/users/admin.principal", headers=csrf_headers)

        assert r.status_code == 400, r.text
        assert "last active administrator" in r.json()["detail"]
        # And the account is still there, untouched.
        remaining = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert "admin.principal" in remaining["users"]

    def test_another_admin_can_still_be_deleted(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        """The guard must not lock ordinary housekeeping: with two admins,
        removing one is legitimate."""
        hasher = admin_module._hasher
        password = hasher.hash("un-mot-de-passe-12345")
        data = {"users": {
            "admin.principal": {
                "disabled": False, "displayname": "Admin",
                "email": "admin@example.com", "password": password,
                "groups": ["admins"],
            },
            "admin.second": {
                "disabled": False, "displayname": "Second",
                "email": "second@example.com", "password": password,
                "groups": ["admins"],
            },
        }}
        tmp_paths["authelia"].write_text(yaml.safe_dump(data))

        r = client.delete("/api/admin/users/admin.second", headers=csrf_headers)

        assert r.status_code == 200, r.text
        remaining = yaml.safe_load(tmp_paths["authelia"].read_text())
        assert "admin.second" not in remaining["users"]
        assert "admin.principal" in remaining["users"]

    def test_a_disabled_admin_does_not_count(
        self, client, tmp_paths, fake_redis, csrf_headers,
    ):
        """A disabled account cannot administer anything: deleting the only
        enabled admin must still be refused, even with a disabled one left."""
        hasher = admin_module._hasher
        password = hasher.hash("un-mot-de-passe-12345")
        data = {"users": {
            "admin.principal": {
                "disabled": False, "displayname": "Admin",
                "email": "admin@example.com", "password": password,
                "groups": ["admins"],
            },
            "admin.dormant": {
                "disabled": True, "displayname": "Dormant",
                "email": "dormant@example.com", "password": password,
                "groups": ["admins"],
            },
        }}
        tmp_paths["authelia"].write_text(yaml.safe_dump(data))

        r = client.delete("/api/admin/users/admin.principal", headers=csrf_headers)
        assert r.status_code == 400, r.text


class TestRestartOrthanc:
    """The route that restarts Orthanc from the panel.

    It goes through a proxy exposing nothing but /containers/<id>/restart.
    What matters here: never announce success without Orthanc having actually
    answered -- a configuration accepted on write may well stop it from
    starting, and the operator must learn that straight away rather than by
    finding a dead PACS later on.
    """

    @pytest.fixture(autouse=True)
    def _no_wait(self, monkeypatch):
        """Neutralise the pauses between probes: the real route waits 60 s."""
        async def _sleep(_):
            return None
        monkeypatch.setattr(admin_module.asyncio, "sleep", _sleep)

    @pytest.fixture
    def _wired(self, monkeypatch):
        monkeypatch.setattr(admin_module, "DOCKER_PROXY_URL", "http://socket-proxy:2375")
        monkeypatch.setattr(admin_module, "ORTHANC_CONTAINER", "orthanc-server")

    def test_unconfigured_proxy_answers_503(
        self, client, tmp_paths, fake_redis, csrf_headers, monkeypatch,
    ):
        """Without the proxy the feature is unavailable, and says so -- rather
        than failing on a connection to an empty URL."""
        monkeypatch.setattr(admin_module, "DOCKER_PROXY_URL", "")
        r = client.post("/api/admin/orthanc/restart", headers=csrf_headers)
        assert r.status_code == 503
        assert "socket-proxy" in r.json()["detail"]

    def test_container_not_found(
        self, client, tmp_paths, fake_redis, csrf_headers, _wired,
    ):
        """A 404 from the proxy means the wrong container name: say it."""
        with respx.mock:
            respx.post("http://socket-proxy:2375/containers/orthanc-server/restart") \
                .respond(status_code=404)
            r = client.post("/api/admin/orthanc/restart", headers=csrf_headers)
        assert r.status_code == 502
        assert "ORTHANC_CONTAINER" in r.json()["detail"]

    def test_restart_refused_by_proxy(
        self, client, tmp_paths, fake_redis, csrf_headers, _wired,
    ):
        """403 = ALLOW_RESTARTS missing. Point at the right cause."""
        with respx.mock:
            respx.post("http://socket-proxy:2375/containers/orthanc-server/restart") \
                .respond(status_code=403)
            r = client.post("/api/admin/orthanc/restart", headers=csrf_headers)
        assert r.status_code == 502
        assert "ALLOW_RESTARTS" in r.json()["detail"]

    def test_succeeds_when_orthanc_answers(
        self, client, tmp_paths, fake_redis, csrf_headers, _wired,
        valid_orthanc_json, redis_sync,
    ):
        with respx.mock:
            respx.post("http://socket-proxy:2375/containers/orthanc-server/restart") \
                .respond(status_code=204)
            respx.get("http://orthanc:8042/system").respond(
                json={"Version": "26.4.2", "Name": "Cuffel PACS"})
            r = client.post("/api/admin/orthanc/restart", headers=csrf_headers)

        assert r.status_code == 200, r.text
        assert r.json()["version"] == "26.4.2"
        assert "warning" not in r.json()
        events = [e[1]["event"] for e in redis_sync.xrange("admin:audit")]
        assert "orthanc.restart.requested" in events
        assert "orthanc.restarted" in events

    def test_restart_that_does_not_apply_our_file_is_reported(
        self, client, tmp_paths, fake_redis, csrf_headers, _wired,
        valid_orthanc_json,
    ):
        """Orthanc answers, but under another name: a compose variable is
        overriding the file. Announcing plain success would be misleading."""
        with respx.mock:
            respx.post("http://socket-proxy:2375/containers/orthanc-server/restart") \
                .respond(status_code=204)
            respx.get("http://orthanc:8042/system").respond(
                json={"Version": "26.4.2", "Name": "Nom Impose Par Le Compose"})
            r = client.post("/api/admin/orthanc/restart", headers=csrf_headers)

        assert r.status_code == 200, r.text
        assert "ORTHANC__*" in r.json()["warning"]

    def test_no_backup_available(
        self, client, tmp_paths, fake_redis, csrf_headers, _wired,
    ):
        """Orthanc stays mute and nothing can be restored: 504, and point at
        the logs rather than pretending."""
        tmp_paths["backups"].mkdir(parents=True, exist_ok=True)
        with respx.mock:
            respx.post("http://socket-proxy:2375/containers/orthanc-server/restart") \
                .respond(status_code=204)
            respx.get("http://orthanc:8042/system").respond(status_code=502)
            r = client.post("/api/admin/orthanc/restart", headers=csrf_headers)

        assert r.status_code == 504
        assert "no backup" in r.json()["detail"]

    def test_configuration_restored_and_orthanc_restarts(
        self, client, tmp_paths, fake_redis, csrf_headers, _wired, redis_sync,
    ):
        """The written configuration prevents Orthanc from starting: restore
        the last backup, restart, and report the refusal -- the PACS is back
        up, which is what matters."""
        tmp_paths["backups"].mkdir(parents=True, exist_ok=True)
        backup = tmp_paths["backups"] / "orthanc.json.bak.20260101-000000"
        backup.write_text(json.dumps({"Name": "Avant"}), encoding="utf-8")
        tmp_paths["orthanc"].write_text(json.dumps({"Name": "Casse"}), encoding="utf-8")

        # Mute for the whole first wait -- 30 probes -- then answering once
        # the backup has been put back. A couple of 502s would not do: the
        # route would find Orthanc alive on the third probe and never roll
        # back at all.
        probes = {"n": 0}

        def _system(request):
            probes["n"] += 1
            if probes["n"] <= admin_module._wait_for_orthanc.__defaults__[0]:
                return httpx.Response(502)
            return httpx.Response(200, json={"Version": "26.4.2"})

        with respx.mock:
            respx.post("http://socket-proxy:2375/containers/orthanc-server/restart") \
                .respond(status_code=204)
            respx.get("http://orthanc:8042/system").mock(side_effect=_system)
            r = client.post("/api/admin/orthanc/restart", headers=csrf_headers)

        assert r.status_code == 502, r.text
        assert "orthanc.json.bak.20260101-000000" in r.json()["detail"]
        # The file really was restored.
        assert json.loads(tmp_paths["orthanc"].read_text())["Name"] == "Avant"
        events = [e[1]["event"] for e in redis_sync.xrange("admin:audit")]
        assert "orthanc.rolled_back" in events


class TestEffectiveConfig:
    """Writing a value does not prove Orthanc applies it.

    Three ways to diverge with nothing to signal it: an ORTHANC__* variable
    from the compose file overriding the file, a field declared at the wrong
    place in the tree, or a restart that never happened. The second is not
    theoretical: StudyListColumns sat under OrthancExplorer2 while Explorer
    reads it under UiOptions, so the setting had never had any effect since it
    existed, and this check is what would have found it.
    """

    def test_no_divergence(self, client, tmp_paths, fake_redis, valid_orthanc_json):
        """What the file declares is what Orthanc reports: nothing to report."""
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.get("/system").respond(json={
                "Name": "Cuffel PACS", "DicomAet": "YOKOINC",
                "DicomPort": 4242, "HttpPort": 8042,
            })
            r = client.get("/api/admin/config-effective")

        assert r.status_code == 200, r.text
        assert r.json()["mismatches"] == []

    def test_divergence_detected(self, client, tmp_paths, fake_redis, valid_orthanc_json):
        """An ORTHANC__* variable overrides the file: say which field, and
        both values -- the operator has to know what actually runs."""
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.get("/system").respond(json={
                "Name": "Nom Impose Par Le Compose", "DicomAet": "YOKOINC",
                "DicomPort": 4242, "HttpPort": 8042,
            })
            r = client.get("/api/admin/config-effective")

        mismatches = r.json()["mismatches"]
        assert len(mismatches) == 1
        assert mismatches[0]["field"] == "Name"
        assert mismatches[0]["in_file"] == "Cuffel PACS"
        assert mismatches[0]["applied_by_orthanc"] == "Nom Impose Par Le Compose"

    def test_field_absent_from_the_file_is_not_a_divergence(
        self, client, tmp_paths, fake_redis,
    ):
        """Not declaring a setting means letting Orthanc apply its default.
        Reporting that as a divergence would drown the real ones."""
        tmp_paths["orthanc"].write_text(json.dumps({"Name": "PACS"}), encoding="utf-8")
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.get("/system").respond(json={
                "Name": "PACS", "DicomAet": "AUTRE", "DicomPort": 11112,
            })
            r = client.get("/api/admin/config-effective")

        assert r.json()["mismatches"] == []

    def test_field_orthanc_does_not_expose_is_ignored(
        self, client, tmp_paths, fake_redis, valid_orthanc_json,
    ):
        """An older Orthanc may not report a field. Absence of proof is not
        proof of divergence."""
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.get("/system").respond(json={"Name": "Cuffel PACS"})
            r = client.get("/api/admin/config-effective")

        assert r.json()["mismatches"] == []

    def test_orthanc_mute_reports_nothing(
        self, client, tmp_paths, fake_redis, valid_orthanc_json,
    ):
        """Orthanc down is a different problem, already surfaced by /health.
        Turning it into a wall of false divergences would help nobody."""
        with respx.mock(base_url="http://orthanc:8042") as mock:
            mock.get("/system").respond(status_code=502)
            r = client.get("/api/admin/config-effective")

        assert r.status_code == 200
        assert r.json()["mismatches"] == []
