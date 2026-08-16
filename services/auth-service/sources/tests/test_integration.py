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
    """Redirect the 3 module-level paths to a per-test tmp_path."""
    authelia = tmp_path / "authelia.yml"
    orthanc = tmp_path / "orthanc.json"
    backups = tmp_path / "backups"
    monkeypatch.setattr(admin_module, "AUTHELIA_YML", authelia)
    monkeypatch.setattr(admin_module, "ORTHANC_JSON", orthanc)
    monkeypatch.setattr(admin_module, "BACKUPS_DIR", backups)
    return {"authelia": authelia, "orthanc": orthanc, "backups": backups}


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
# Test 2: CF Access rotate + verify pipeline
# ============================================================================

class TestCFAccess:

    def test_rotate_then_verify_matches(self, client, fake_redis, csrf_headers):
        """POST rotate -> GET verify-cf with the new headers = 204."""
        r = client.post("/api/admin/cf-access/rotate", json={
            "client_id": "new-id-ec87a9cb.access",
            "client_secret": "s" * 64,
        }, headers=csrf_headers)
        assert r.status_code == 200

        # Verify with the new headers
        r = client.get("/api/internal/verify-cf", headers={
            "x-cf-client-id": "new-id-ec87a9cb.access",
            "x-cf-client-secret": "s" * 64,
        })
        assert r.status_code == 204

    def test_verify_wrong_secret_rejected(self, client, fake_redis, csrf_headers):
        """Verify with a wrong secret = 403."""
        # Rotate first (client_id min 10 chars per Field validation)
        client.post("/api/admin/cf-access/rotate", json={
            "client_id": "id-abc-with-length.access",
            "client_secret": "s" * 64,
        }, headers=csrf_headers)

        # Wrong secret
        r = client.get("/api/internal/verify-cf", headers={
            "x-cf-client-id": "id-abc-with-length.access",
            "x-cf-client-secret": "w" * 64,
        })
        assert r.status_code == 403

    def test_verify_no_config_returns_503(self, client, fake_redis):
        """Verify against an empty Redis = 503 (not configured)."""
        r = client.get("/api/internal/verify-cf", headers={
            "x-cf-client-id": "any",
            "x-cf-client-secret": "any",
        })
        assert r.status_code == 503

    def test_rotate_snapshots_old_to_history(
        self, client, fake_redis, redis_sync, csrf_headers,
    ):
        """The old pair is pushed to cf_access:history on rotate."""
        # 1st rotate (client_id min 10 chars)
        r1 = client.post("/api/admin/cf-access/rotate", json={
            "client_id": "id-one-abc.access",
            "client_secret": "1" * 64,
        }, headers=csrf_headers)
        assert r1.status_code == 200, r1.text
        # 2nd rotate
        r2 = client.post("/api/admin/cf-access/rotate", json={
            "client_id": "id-two-abc.access",
            "client_secret": "2" * 64,
        }, headers=csrf_headers)
        assert r2.status_code == 200, r2.text

        # History holds at least the old pair
        length = redis_sync.llen("cf_access:history")
        assert length >= 1
        first = redis_sync.lindex("cf_access:history", 0)
        assert "id-one-abc.access" in first
        assert "1" * 64 in first


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

            r = client.patch("/api/admin/orthanc/config", json={
                "changes": {"Name": "New PACS Name", "HttpCompressionEnabled": True},
            }, headers=csrf_headers)
            assert r.status_code == 200, r.text
            assert reset_route.called

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
                barrier.wait()  # signale au test qu'on tient le lock
                time.sleep(3)   # hold plus longtemps que le timeout endpoint

        holder = threading.Thread(target=hold_lock)
        holder.start()
        try:
            barrier.wait()  # attend que hold_lock ait le lock

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
# Test 9: Redis down = fail closed on verify-cf
# ============================================================================

class TestRedisResilience:

    def test_verify_cf_fail_closed_on_redis_error(self, app, tmp_paths, monkeypatch):
        """If Redis raises RedisError, verify-cf returns 403 not 500."""
        from redis.exceptions import RedisError

        class BrokenRedis:
            async def get(self, k):
                raise RedisError("simulated blackout")

            async def incr(self, k):
                raise RedisError("simulated blackout")

        admin_module.set_redis(BrokenRedis())
        c = TestClient(app)
        r = c.get("/api/internal/verify-cf", headers={
            "x-cf-client-id": "any", "x-cf-client-secret": "any",
        })
        assert r.status_code == 403


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
