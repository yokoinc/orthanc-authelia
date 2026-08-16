"""
Unit tests for admin_module.py.

Focused on the invariants that guard against lockout / corruption:
  - _validate_authelia refuses a YAML without an active admin
  - _apply_scalar_change refuses to overwrite a dict/array
  - _validate_orthanc refuses turning the *InDatabase flags off
  - argon2 round-trip hash + verify

Run with:
    cd services/auth-service/sources
    python -m pytest tests/test_admin_module.py -v
"""

import pytest


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def valid_authelia_data():
    return {
        "users": {
            "cuffel.gregory": {
                "disabled": False,
                "displayname": "Gregory Cuffel",
                "email": "cuffel.gregory@gmail.com",
                "password": "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQxMjM$dGVzdA",
                "groups": ["admins", "doctors"],
            },
        },
    }


@pytest.fixture
def valid_orthanc_config():
    return {
        "Name": "Cuffel PACS",
        "DicomAet": "YOKOINC",
        "DicomModalitiesInDatabase": True,
        "OrthancPeersInDatabase": True,
        "DicomWeb": {"Enable": True, "StowMaxSize": 500},
    }


# ============================================================================
# _validate_authelia: anti-lockout invariants
# ============================================================================

class TestValidateAuthelia:

    def test_valid_data_passes(self, valid_authelia_data):
        from admin_module import _validate_authelia
        _validate_authelia(valid_authelia_data)  # no raise

    def test_empty_users_refused(self):
        from admin_module import _validate_authelia
        with pytest.raises(ValueError, match="empty or missing"):
            _validate_authelia({"users": {}})

    def test_missing_users_key_refused(self):
        from admin_module import _validate_authelia
        with pytest.raises(ValueError, match="empty or missing"):
            _validate_authelia({})

    def test_no_admin_refused(self, valid_authelia_data):
        from admin_module import _validate_authelia
        valid_authelia_data["users"]["cuffel.gregory"]["groups"] = ["doctors"]
        with pytest.raises(ValueError, match="active admin required"):
            _validate_authelia(valid_authelia_data)

    def test_disabled_admin_doesnt_count(self, valid_authelia_data):
        from admin_module import _validate_authelia
        valid_authelia_data["users"]["cuffel.gregory"]["disabled"] = True
        with pytest.raises(ValueError, match="active admin required"):
            _validate_authelia(valid_authelia_data)

    def test_missing_password_field(self, valid_authelia_data):
        from admin_module import _validate_authelia
        del valid_authelia_data["users"]["cuffel.gregory"]["password"]
        with pytest.raises(ValueError, match="password.*missing"):
            _validate_authelia(valid_authelia_data)

    def test_password_must_be_argon2id(self, valid_authelia_data):
        from admin_module import _validate_authelia
        valid_authelia_data["users"]["cuffel.gregory"]["password"] = "$bcrypt$..."
        with pytest.raises(ValueError, match="argon2id"):
            _validate_authelia(valid_authelia_data)


# ============================================================================
# _apply_scalar_change: refuses to overwrite dict/array
# ============================================================================

class TestApplyScalarChange:

    def test_top_level_scalar(self):
        from admin_module import _apply_scalar_change
        cfg = {"Name": "ORTHANC"}
        _apply_scalar_change(cfg, "Name", "Cuffel")
        assert cfg["Name"] == "Cuffel"

    def test_dotted_path_scalar(self):
        from admin_module import _apply_scalar_change
        cfg = {"DicomWeb": {"Enable": True}}
        _apply_scalar_change(cfg, "DicomWeb.Enable", False)
        assert cfg["DicomWeb"]["Enable"] is False

    def test_creates_nested_path_if_missing(self):
        from admin_module import _apply_scalar_change
        cfg = {}
        _apply_scalar_change(cfg, "DicomWeb.Enable", True)
        assert cfg == {"DicomWeb": {"Enable": True}}

    def test_refuses_non_whitelisted_path(self):
        from admin_module import _apply_scalar_change
        cfg = {}
        with pytest.raises(ValueError, match="not editable"):
            _apply_scalar_change(cfg, "PostgreSQL.Password", "secret")

    def test_refuses_wrong_type(self):
        from admin_module import _apply_scalar_change
        cfg = {}
        with pytest.raises(ValueError, match="expected"):
            _apply_scalar_change(cfg, "DicomPort", "not_an_int")

    def test_dicomaet_max_16_chars(self):
        from admin_module import _apply_scalar_change
        cfg = {}
        with pytest.raises(ValueError, match="16 characters max"):
            _apply_scalar_change(cfg, "DicomAet", "TOOLONGAETLABEL_XX")

    def test_dicomaet_16_chars_ok(self):
        from admin_module import _apply_scalar_change
        cfg = {}
        _apply_scalar_change(cfg, "DicomAet", "SIXTEENCHARS_OKA")
        assert cfg["DicomAet"] == "SIXTEENCHARS_OKA"


# ============================================================================
# _validate_orthanc: critical flags
# ============================================================================

class TestValidateOrthanc:

    def test_valid_config_passes(self, valid_orthanc_config):
        from admin_module import _validate_orthanc
        _validate_orthanc(valid_orthanc_config)  # no raise

    def test_disabling_modalities_in_db_refused(self, valid_orthanc_config):
        """Turning the flag off when it was on = refused."""
        from admin_module import _validate_orthanc
        before = dict(valid_orthanc_config)
        after = dict(valid_orthanc_config, DicomModalitiesInDatabase=False)
        with pytest.raises(ValueError, match="DicomModalitiesInDatabase"):
            _validate_orthanc(after, before)

    def test_disabling_peers_in_db_refused(self, valid_orthanc_config):
        from admin_module import _validate_orthanc
        before = dict(valid_orthanc_config)
        after = dict(valid_orthanc_config, OrthancPeersInDatabase=False)
        with pytest.raises(ValueError, match="OrthancPeersInDatabase"):
            _validate_orthanc(after, before)

    def test_flags_absent_before_are_not_imposed(self, valid_orthanc_config):
        """Flags absent from the original config: we do not require them.

        Many installs never set these two keys. Making them mandatory would
        fail any unrelated change with a 400 -- a simple Name change, say.
        """
        from admin_module import _validate_orthanc
        before = {k: v for k, v in valid_orthanc_config.items()
                  if k not in ("DicomModalitiesInDatabase", "OrthancPeersInDatabase")}
        after = dict(before, Name="New Name")
        _validate_orthanc(after, before)  # does not raise

    def test_dicomaet_too_long_refused(self, valid_orthanc_config):
        from admin_module import _validate_orthanc
        valid_orthanc_config["DicomAet"] = "THIS_STRING_IS_WAY_TOO_LONG"
        with pytest.raises(ValueError, match="16"):
            _validate_orthanc(valid_orthanc_config)


# ============================================================================
# argon2 round-trip (third-party library, but let us check our wiring)
# ============================================================================

class TestArgon2:

    def test_hash_starts_with_expected_prefix(self):
        from admin_module import _hasher
        h = _hasher.hash("mysupersecretpassword")
        assert h.startswith("$argon2id$")

    def test_hash_verify_roundtrip(self):
        from admin_module import _hasher
        h = _hasher.hash("mysupersecretpassword")
        _hasher.verify(h, "mysupersecretpassword")  # no raise

    def test_verify_wrong_password_raises(self):
        from admin_module import _hasher
        from argon2.exceptions import VerifyMismatchError
        h = _hasher.hash("correct")
        with pytest.raises(VerifyMismatchError):
            _hasher.verify(h, "wrong")

    def test_two_hashes_of_same_password_differ(self):
        """Random salt = every hash unique even for the same password."""
        from admin_module import _hasher
        h1 = _hasher.hash("test")
        h2 = _hasher.hash("test")
        assert h1 != h2


# ============================================================================
# CSRF token (double-submit)
# ============================================================================

class TestCSRF:

    def test_token_length(self):
        import secrets
        # Mimics what issue_csrf_cookie does
        token = secrets.token_urlsafe(32)
        assert len(token) >= 40  # 32 bytes urlsafe = ~43 chars

    def test_compare_digest_matches(self):
        import secrets
        t = secrets.token_urlsafe(32)
        assert secrets.compare_digest(t, t)

    def test_compare_digest_rejects_diff(self):
        import secrets
        assert not secrets.compare_digest("abc", "abd")
