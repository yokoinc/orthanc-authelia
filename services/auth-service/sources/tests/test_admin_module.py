"""
Unit tests for admin_module.py.

Focused on the invariants that guard against lockout and corruption:
  - _validate_authelia refuses a YAML without an active administrator
  - _apply_scalar_change refuses to overwrite a dict or array
  - _validate_orthanc refuses disabling the *InDatabase flags
  - argon2 round-trip: hash then verify

Run with:
    cd services/auth-service/sources
    python -m pytest tests/test_admin_module.py -v
"""

import asyncio
import json
from pathlib import Path

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
                "groups": ["admin", "doctors"],
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
# _validate_authelia : invariants anti-lockout
# ============================================================================

class TestValidateAuthelia:

    def test_valid_data_passes(self, valid_authelia_data):
        from admin_module import _validate_authelia
        _validate_authelia(valid_authelia_data)  # no raise

    def test_empty_users_refused(self):
        from admin_module import _validate_authelia
        with pytest.raises(ValueError, match="vide ou absente"):
            _validate_authelia({"users": {}})

    def test_missing_users_key_refused(self):
        from admin_module import _validate_authelia
        with pytest.raises(ValueError, match="vide ou absente"):
            _validate_authelia({})

    def test_no_admin_refused(self, valid_authelia_data):
        from admin_module import _validate_authelia
        valid_authelia_data["users"]["cuffel.gregory"]["groups"] = ["doctors"]
        with pytest.raises(ValueError, match="admin actif requis"):
            _validate_authelia(valid_authelia_data)

    def test_disabled_admin_doesnt_count(self, valid_authelia_data):
        from admin_module import _validate_authelia
        valid_authelia_data["users"]["cuffel.gregory"]["disabled"] = True
        with pytest.raises(ValueError, match="admin actif requis"):
            _validate_authelia(valid_authelia_data)

    def test_missing_password_field(self, valid_authelia_data):
        from admin_module import _validate_authelia
        del valid_authelia_data["users"]["cuffel.gregory"]["password"]
        with pytest.raises(ValueError, match="password.*manquant"):
            _validate_authelia(valid_authelia_data)

    def test_password_must_be_argon2id(self, valid_authelia_data):
        from admin_module import _validate_authelia
        valid_authelia_data["users"]["cuffel.gregory"]["password"] = "$bcrypt$..."
        with pytest.raises(ValueError, match="argon2id"):
            _validate_authelia(valid_authelia_data)


# ============================================================================
# _apply_scalar_change : refuse d'ecraser dict/array
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
        with pytest.raises(ValueError, match="non editable"):
            _apply_scalar_change(cfg, "PostgreSQL.Password", "secret")

    def test_refuses_wrong_type(self):
        from admin_module import _apply_scalar_change
        cfg = {}
        with pytest.raises(ValueError, match="attendu"):
            _apply_scalar_change(cfg, "DicomPort", "not_an_int")

    def test_dicomaet_max_16_chars(self):
        from admin_module import _apply_scalar_change
        cfg = {}
        with pytest.raises(ValueError, match="max 16"):
            _apply_scalar_change(cfg, "DicomAet", "TOOLONGAETLABEL_XX")

    def test_dicomaet_16_chars_ok(self):
        from admin_module import _apply_scalar_change
        cfg = {}
        _apply_scalar_change(cfg, "DicomAet", "SIXTEENCHARS_OKA")
        assert cfg["DicomAet"] == "SIXTEENCHARS_OKA"


# ============================================================================
# _validate_orthanc : flags critiques
# ============================================================================

class TestValidateOrthanc:

    def test_valid_config_passes(self, valid_orthanc_config):
        from admin_module import _validate_orthanc
        _validate_orthanc(valid_orthanc_config)  # no raise

    def test_disabling_modalities_in_db_refused(self, valid_orthanc_config):
        from admin_module import _validate_orthanc
        valid_orthanc_config["DicomModalitiesInDatabase"] = False
        with pytest.raises(ValueError, match="DicomModalitiesInDatabase"):
            _validate_orthanc(valid_orthanc_config)

    def test_disabling_peers_in_db_refused(self, valid_orthanc_config):
        from admin_module import _validate_orthanc
        valid_orthanc_config["OrthancPeersInDatabase"] = False
        with pytest.raises(ValueError, match="OrthancPeersInDatabase"):
            _validate_orthanc(valid_orthanc_config)

    def test_dicomaet_too_long_refused(self, valid_orthanc_config):
        from admin_module import _validate_orthanc
        valid_orthanc_config["DicomAet"] = "THIS_STRING_IS_WAY_TOO_LONG"
        with pytest.raises(ValueError, match="16"):
            _validate_orthanc(valid_orthanc_config)


# ============================================================================
# argon2 round-trip (third-party library, but check our wiring)
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
        """Salt aleatoire = chaque hash unique meme pour le meme password."""
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


# ============================================================================
# JSON comments: Orthanc accepts them, json.loads does not
# ============================================================================

class TestStripJsonComments:

    def test_line_comment_removed(self):
        from admin_module import _strip_json_comments
        import json
        raw = """{
  // a comment
  "a": 1
}"""
        assert json.loads(_strip_json_comments(raw)) == {"a": 1}

    def test_block_comment_removed(self):
        from admin_module import _strip_json_comments
        import json
        raw = """{
  /* sur
     plusieurs lignes */
  "a": 1
}"""
        assert json.loads(_strip_json_comments(raw)) == {"a": 1}

    def test_url_double_slash_preserved(self):
        """A URL's // must not be mistaken for a comment."""
        from admin_module import _strip_json_comments
        import json
        raw = '{"url": "http://auth-service:8000"}'
        out = json.loads(_strip_json_comments(raw))
        assert out["url"] == "http://auth-service:8000"

    def test_slashes_inside_string_preserved(self):
        from admin_module import _strip_json_comments
        import json
        raw = '{"path": "a//b", "glob": "/* not a comment */"}'
        out = json.loads(_strip_json_comments(raw))
        assert out["path"] == "a//b"
        assert out["glob"] == "/* not a comment */"

    def test_escaped_quote_inside_string(self):
        """An escaped quote must not end the string prematurely."""
        from admin_module import _strip_json_comments
        import json
        raw = '{"quoted": "il a dit \\"bonjour\\"", "n": 1}'
        out = json.loads(_strip_json_comments(raw))
        assert out["n"] == 1

    def test_real_orthanc_config_shape(self):
        """Real case: leading comments and a URL with // in the same config."""
        from admin_module import _strip_json_comments
        import json
        raw = """{
  // =====================================================
  // ORTHANC PACS SERVER CONFIGURATION
  // =====================================================
  "Name": "Orthanc",
  "Authorization": {
    "WebServiceRootUrl": "http://auth-service:8000"
  }
}"""
        out = json.loads(_strip_json_comments(raw))
        assert out["Name"] == "Orthanc"
        assert out["Authorization"]["WebServiceRootUrl"] == "http://auth-service:8000"


# ============================================================================
# restart_orthanc: restarting through the Docker proxy
# ============================================================================

class TestRestartOrthanc:
    """The route that restarts Orthanc from the panel.

    It calls a proxy exposing nothing but /containers/<id>/restart. What
    matters here: never announce success without Orthanc having actually
    answered -- a configuration accepted on write may well stop it from
    restarting, and the operator must learn that straight away.
    """

    @staticmethod
    def _admin():
        from admin_module import AdminUser
        return AdminUser(username="admin", groups=["admin"])

    @pytest.fixture(autouse=True)
    def _without_side_effects(self, monkeypatch):
        """Neutralise l'audit (Redis) et les attentes entre deux sondages."""
        import admin_module

        async def _audit_muet(*a, **k):
            return None

        async def _sans_attente(_):
            return None

        monkeypatch.setattr(admin_module, "_audit", _audit_muet)
        monkeypatch.setattr(admin_module.asyncio, "sleep", _sans_attente)

    def test_unconfigured_proxy_answers_503(self, monkeypatch):
        """Without DOCKER_PROXY_URL the feature is unavailable, not broken."""
        import admin_module
        from fastapi import HTTPException

        monkeypatch.setattr(admin_module, "DOCKER_PROXY_URL", "")

        with pytest.raises(HTTPException) as e:
            _run(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 503
        assert "DOCKER_PROXY_URL" in e.value.detail

    def test_container_not_found(self, monkeypatch):
        """404 du proxy = mauvais nom de conteneur : le dire explicitement."""
        import admin_module
        from fastapi import HTTPException

        self._wire_proxy(monkeypatch, 404)

        with pytest.raises(HTTPException) as e:
            _run(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 502
        assert "ORTHANC_CONTAINER" in e.value.detail

    def test_restart_refused_by_proxy(self, monkeypatch):
        """403 = ALLOW_RESTARTS absent. Orienter vers la bonne cause."""
        import admin_module
        from fastapi import HTTPException

        self._wire_proxy(monkeypatch, 403)

        with pytest.raises(HTTPException) as e:
            _run(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 502
        assert "ALLOW_RESTARTS" in e.value.detail

    def test_succeeds_when_orthanc_answers(self, monkeypatch):
        """The nominal case: 204 from the proxy, then /system answering 200."""
        import admin_module

        self._wire_proxy(monkeypatch, 204)
        self._wire_system(monkeypatch, [200])

        r = _run(admin_module.restart_orthanc(self._admin()))
        assert r["ok"] is True
        assert r["version"] == "1.12.11"

    def test_succeeds_after_a_few_probes(self, monkeypatch):
        """Orthanc ouvre son port avant d'etre pret : on attend qu'il reponde."""
        import admin_module

        self._wire_proxy(monkeypatch, 204)
        self._wire_system(monkeypatch, [502, 502, 200])

        r = _run(admin_module.restart_orthanc(self._admin()))
        assert r["ok"] is True

    def test_orthanc_never_comes_back(self, monkeypatch):
        """Le point important : pas de faux succes si Orthanc reste muet."""
        import admin_module
        from fastapi import HTTPException

        self._wire_proxy(monkeypatch, 204)
        self._wire_system(monkeypatch, [502] * 40)

        with pytest.raises(HTTPException) as e:
            _run(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 504
        assert "journaux" in e.value.detail

    # --- outillage ---------------------------------------------------------

    @staticmethod
    def _wire_proxy(monkeypatch, code: int):
        """Remplace l'appel HTTP au proxy par une reponse au code voulu."""
        import admin_module

        monkeypatch.setattr(admin_module, "DOCKER_PROXY_URL", "http://proxy:2375")

        class _Reponse:
            status_code = code

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                return _Reponse()

        monkeypatch.setattr(admin_module.httpx, "AsyncClient", _Client)

    @staticmethod
    def _wire_system(monkeypatch, codes: list):
        """Fait repondre /system selon la suite de codes donnee."""
        import admin_module

        restants = list(codes)

        class _Reponse:
            def __init__(self, code):
                self.status_code = code

            @staticmethod
            def json():
                return {"Name": "PACS", "Version": "1.12.11"}

        async def _faux_orthanc(_methode, _chemin, **_k):
            return _Reponse(restants.pop(0) if restants else 502)

        monkeypatch.setattr(admin_module, "_orthanc", _faux_orthanc)


def _run(coro):
    """Execute une coroutine dans une boucle neuve, fermee ensuite."""
    boucle = asyncio.new_event_loop()
    try:
        return boucle.run_until_complete(coro)
    finally:
        boucle.close()


# ============================================================================
# Writing orthanc.json: preserving what the structure does not carry
# ============================================================================

class TestNonDestructiveWrite:
    """The panel edits the text rather than regenerating the file.

    A rewrite through json.dumps() erases comments, ordering and grouping.
    Observed on a real installation: the first change made from the panel
    had removed the file's 44 comments, that is to say most of its
    documentation.

    The cases gathered here are the ones a naive textual edit gets wrong: a
    key name quoted in a comment, a brace inside a string, a comment stuck
    against the value.
    """

    @staticmethod
    def _read_back(texte):
        from admin_module import _strip_json_comments
        return json.loads(_strip_json_comments(texte))

    def test_comments_preserved(self):
        from admin_module import _apply_text_changes
        source = """{
  // Name shown in the interface
  "Name": "Orthanc",

  // Titre applicatif DICOM, 16 caracteres au plus
  "DicomAet": "ORTHANC"
}"""
        out = _apply_text_changes(source, {"Name": "PACS"})
        assert out.count("//") == 2
        assert "Name shown in the interface" in out
        assert self._read_back(out) == {"Name": "PACS", "DicomAet": "ORTHANC"}

    def test_only_targeted_line_changes(self):
        """A change must not reformat the rest of the file."""
        from admin_module import _apply_text_changes
        source = '{\n  "A": 1,\n  "B": 2,\n  "C": 3\n}'
        out = _apply_text_changes(source, {"B": 20})
        avant, apres = source.splitlines(), out.splitlines()
        assert len(avant) == len(apres)
        assert [i for i, (a, b) in enumerate(zip(avant, apres)) if a != b] == [2]

    def test_key_quoted_in_a_comment(self):
        """Le piege classique : le nom de la cle apparait aussi en commentaire."""
        from admin_module import _apply_text_changes
        source = """{
  // Not to be confused with the "Name" of the DicomWeb block below
  "Name": "Orthanc"
}"""
        out = _apply_text_changes(source, {"Name": "PACS"})
        assert 'the "Name" of the DicomWeb' in out       # the comment is intact
        assert self._read_back(out) == {"Name": "PACS"}

    def test_brace_inside_a_string(self):
        """A brace between quotes must not be read as a block."""
        from admin_module import _apply_text_changes
        source = '{\n  "Motif": "prefixe{suffixe}",\n  "Name": "Orthanc"\n}'
        out = _apply_text_changes(source, {"Name": "PACS"})
        assert self._read_back(out) == {"Motif": "prefixe{suffixe}", "Name": "PACS"}

    def test_end_of_line_comment(self):
        """The value stops before the //, which must survive untouched."""
        from admin_module import _apply_text_changes
        source = '{\n  "Taille": 500, // en megaoctets\n  "Name": "Orthanc"\n}'
        out = _apply_text_changes(source, {"Taille": 800})
        assert "// en megaoctets" in out
        assert self._read_back(out)["Taille"] == 800

    def test_nested_key(self):
        from admin_module import _apply_text_changes
        source = """{
  "Name": "Orthanc",
  "DicomWeb": {
    // Maximum size of a STOW-RS upload
    "StowMaxSize": 500,
    "Enable": true
  }
}"""
        out = _apply_text_changes(source, {"DicomWeb.StowMaxSize": 1000})
        assert "Maximum size" in out
        assert self._read_back(out)["DicomWeb"] == {"StowMaxSize": 1000, "Enable": True}

    def test_missing_key_appended(self):
        """Orthanc leaves many settings implicit: defining them is a common case,
        not an exception."""
        from admin_module import _apply_text_changes
        source = '{\n  // Reglages de base\n  "Name": "Orthanc"\n}'
        out = _apply_text_changes(source, {"DicomAlwaysAllowStore": False})
        assert "// Reglages de base" in out
        assert self._read_back(out) == {"Name": "Orthanc", "DicomAlwaysAllowStore": False}
        # Same indentation as its neighbours: a misaligned key stands out,
        # and makes the file look hand-edited in a hurry.
        ligne = [l for l in out.splitlines() if "DicomAlwaysAllowStore" in l][0]
        assert ligne.startswith('  "'), repr(ligne)

    def test_key_added_after_trailing_comment(self):
        """Le commentaire de fin de bloc doit rester en dernier."""
        from admin_module import _apply_text_changes
        source = '{\n  "Name": "Orthanc"\n  // fin du bloc\n}'
        out = _apply_text_changes(source, {"DicomAet": "PACS"})
        assert self._read_back(out) == {"Name": "Orthanc", "DicomAet": "PACS"}
        assert out.index('"DicomAet"') < out.index("// fin du bloc")

    def test_append_into_nested_object(self):
        from admin_module import _apply_text_changes
        source = '{\n  "DicomWeb": {\n    "Enable": true\n  }\n}'
        out = _apply_text_changes(source, {"DicomWeb.StowMaxSize": 500})
        assert self._read_back(out)["DicomWeb"] == {"Enable": True, "StowMaxSize": 500}
        ligne = [l for l in out.splitlines() if "StowMaxSize" in l][0]
        assert ligne.startswith('    "'), repr(ligne)

    def test_missing_parent_refused(self):
        """Creer une arborescence demanderait de deviner une mise en forme :
        on prefere le signaler et laisser l'appelant regenerer."""
        from admin_module import _apply_text_changes
        with pytest.raises(ValueError, match="parent absent"):
            _apply_text_changes('{\n  "Name": "Orthanc"\n}',
                                {"Absent.Cle": 1})

    def test_several_changes_at_once(self):
        from admin_module import _apply_text_changes
        source = """{
  // en-tete
  "Name": "Orthanc",
  "DicomAet": "ORTHANC",
  "DicomPort": 4242
}"""
        out = _apply_text_changes(source, {
            "Name": "PACS", "DicomPort": 11112, "DicomCheckCalledAet": True,
        })
        assert "// en-tete" in out
        assert self._read_back(out) == {
            "Name": "PACS", "DicomAet": "ORTHANC", "DicomPort": 11112,
            "DicomCheckCalledAet": True,
        }

    def test_scalar_types(self):
        """booleen, entier, chaine et null doivent se relire a l'identique."""
        from admin_module import _apply_text_changes
        source = '{\n  "A": 1,\n  "B": "x",\n  "C": true,\n  "D": null\n}'
        out = _apply_text_changes(source, {"A": 42, "B": "y", "C": False, "D": "z"})
        assert self._read_back(out) == {"A": 42, "B": "y", "C": False, "D": "z"}

    def test_real_repository_file(self):
        """The shipped file: no comment may disappear.

        The file is found by walking up the tree rather than at a fixed depth:
        depending on whether the whole repository or only sources/ is mounted,
        the number of levels differs. A frozen index made CI fail while the
        suite passed locally.
        """
        from admin_module import _apply_text_changes
        from pathlib import Path as _P

        exemple = next(
            (parent / "orthanc.json.example"
             for parent in _P(__file__).resolve().parents
             if (parent / "orthanc.json.example").exists()),
            None,
        )
        if exemple is None:                # depot non monte (arborescence reduite)
            pytest.skip("orthanc.json.example hors de l'arborescence")

        source = exemple.read_text(encoding="utf-8")
        avant = source.count("//")
        out = _apply_text_changes(source, {"Name": "PACS Cuffel"})
        assert out.count("//") == avant
        assert self._read_back(out)["Name"] == "PACS Cuffel"


# ============================================================================
# Default viewer for share links
# ============================================================================

class TestShareViewer:
    """The viewer preselected when sharing a study.

    These tests were redone: the previous ones checked that a value written
    into the settings was read back, without ever establishing that anyone
    consults it. Nobody did -- Explorer reads
    OrthancExplorer2.Tokens.ShareType from orthanc.json, and its bundle holds
    no occurrence of the "default-viewer" that /settings/roles returned. The
    setting wrote, read back, and changed nothing on screen.

    Hence the guard below: the targeted path must stay the one Explorer
    reads.
    """

    @pytest.fixture
    def orthanc_config(self, tmp_path, monkeypatch):
        import admin_module

        fichier = tmp_path / "orthanc.json"
        fichier.write_text(
            '{\n'
            '  // Interface web\n'
            '  "OrthancExplorer2": {\n'
            '    "Tokens": {\n'
            '      "ShareType": "volview-viewer-publication"\n'
            '    }\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(admin_module, "ORTHANC_JSON", fichier)
        return fichier

    def test_targets_the_field_explorer_reads(self):
        """Guard: Explorer does `tokenType: this.tokens.ShareType`.

        Should this path leave the editable fields, the setting becomes
        ineffective again -- silently, since it would keep writing and reading
        back correctly.
        """
        from admin_module import ORTHANC_EDITABLE_PATHS
        assert "OrthancExplorer2.Tokens.ShareType" in ORTHANC_EDITABLE_PATHS

    def test_read_from_orthanc_json(self, orthanc_config):
        from admin_module import _read_share_type
        assert _read_share_type() == "volview-viewer-publication"

    def test_unknown_value_ignored(self, orthanc_config):
        """A value outside the list must not break the share menu."""
        from admin_module import _read_share_type
        orthanc_config.write_text(
            '{"OrthancExplorer2": {"Tokens": {"ShareType": "nimporte-quoi"}}}',
            encoding="utf-8")
        assert _read_share_type() == "ohif-viewer-publication"

    def test_missing_field(self, orthanc_config):
        from admin_module import _read_share_type
        orthanc_config.write_text('{"Name": "PACS"}', encoding="utf-8")
        assert _read_share_type() == "ohif-viewer-publication"

    def test_unreadable_file(self, tmp_path, monkeypatch):
        import admin_module
        from admin_module import _read_share_type
        monkeypatch.setattr(admin_module, "ORTHANC_JSON",
                            tmp_path / "absent.json")
        assert _read_share_type() == "ohif-viewer-publication"

    def test_write_preserves_comments(self, orthanc_config):
        """The write goes through the same machinery as the rest of the config."""
        from admin_module import _apply_text_changes, _strip_json_comments
        import json as _json

        source = orthanc_config.read_text(encoding="utf-8")
        out = _apply_text_changes(
            source,
            {"OrthancExplorer2.Tokens.ShareType": "ohif-viewer-publication"},
        )
        assert "// Interface web" in out
        relu = _json.loads(_strip_json_comments(out))
        assert relu["OrthancExplorer2"]["Tokens"]["ShareType"] == "ohif-viewer-publication"

    def test_auth_service_returns_same_value(self, orthanc_config):
        """/settings/roles must not contradict what actually applies."""
        import auth_service
        assert auth_service._default_share_viewer() == "volview-viewer-publication"


# ============================================================================
# Magasin de settings_file applicatifs
# ============================================================================

class TestSettingsStore:
    """Settings only the panel uses live outside .env.

    .env exists only for what docker compose must know before starting a
    container. Housing an interface preference there forces mounting it
    writable, rewriting it in place, and mixes labels with passwords.
    """

    @pytest.fixture
    def settings_file(self, tmp_path, monkeypatch):
        import admin_module

        fichier = tmp_path / "app-settings" / "settings.json"
        monkeypatch.setattr(admin_module, "SETTINGS_FILE", fichier)
        monkeypatch.setattr(admin_module, "ENV_FILE", tmp_path / ".env")
        return fichier

    def test_write_then_read(self, settings_file):
        from admin_module import _write_setting, _read_setting
        _write_setting("share_default_viewer", "stone-viewer-publication")
        assert _read_setting("share_default_viewer") == "stone-viewer-publication"

    def test_directory_created_when_needed(self, settings_file):
        """A fresh installation does not have the file yet."""
        from admin_module import _write_setting
        assert not settings_file.parent.exists()
        _write_setting("langue", "fr")
        assert settings_file.exists()

    def test_several_settings_coexist(self, settings_file):
        from admin_module import _write_setting, _read_setting
        _write_setting("a", 1)
        _write_setting("b", "deux")
        assert (_read_setting("a"), _read_setting("b")) == (1, "deux")

    def test_falls_back_to_former_env_var(self, settings_file, tmp_path):
        """An existing installation has the setting in its .env: it must keep
        applying until it gets redefined."""
        from admin_module import _read_setting
        (tmp_path / ".env").write_text(
            "SHARE_DEFAULT_VIEWER=stone-viewer-publication\n", encoding="utf-8")
        assert _read_setting("share_default_viewer",
                             "SHARE_DEFAULT_VIEWER") == "stone-viewer-publication"

    def test_file_wins_over_env(self, settings_file, tmp_path):
        """Apres la premiere ecriture, la ligne du .env devient inerte."""
        from admin_module import _write_setting, _read_setting
        (tmp_path / ".env").write_text(
            "SHARE_DEFAULT_VIEWER=stone-viewer-publication\n", encoding="utf-8")
        _write_setting("share_default_viewer", "volview-viewer-publication")
        assert _read_setting("share_default_viewer",
                             "SHARE_DEFAULT_VIEWER") == "volview-viewer-publication"

    def test_unreadable_file_degrades(self, settings_file):
        """Corrupt JSON must degrade to the defaults, not stop the service from
        answering."""
        from admin_module import _read_setting
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text("{ceci n'est pas du JSON", encoding="utf-8")
        assert _read_setting("share_default_viewer", default="ohif") == "ohif"

    def test_atomic_write(self, settings_file):
        """Aucun fichier temporaire ne doit subsister apres l'ecriture."""
        from admin_module import _write_setting
        _write_setting("a", 1)
        restes = [f.name for f in settings_file.parent.iterdir()
                  if f.name != "settings.json"]
        assert restes == [], restes

    def test_no_secret_in_the_file(self, settings_file):
        """Design guard: this file is not a vault. It lives under data/, escapes
        the .gitignore meant for secrets, and could be copied around without
        care."""
        from admin_module import _write_setting
        _write_setting("share_default_viewer", "ohif-viewer-publication")
        contenu = settings_file.read_text(encoding="utf-8").lower()
        for interdit in ("password", "secret", "token", "_key"):
            assert interdit not in contenu, interdit


# ============================================================================
# Langue de l'interface
# ============================================================================

class TestLanguage:
    """The language used to be frozen at module load, from .env.

    Changing it meant recreating the container, for a display preference.
    Translations are now resolved at display time, which allows changing it
    from the panel.
    """

    @pytest.fixture
    def settings_file(self, tmp_path, monkeypatch):
        import admin_module
        import auth_service

        fichier = tmp_path / "app-settings" / "settings.json"
        monkeypatch.setattr(admin_module, "SETTINGS_FILE", fichier)
        monkeypatch.setattr(admin_module, "ENV_FILE", tmp_path / ".env")
        monkeypatch.delenv("LANGUAGE", raising=False)
        # The translations cache survives from one test to the next.
        auth_service._translations_cache["langue"] = None
        return fichier

    def test_defaults_to_english(self, settings_file):
        import auth_service
        assert auth_service._language() == "en"

    def test_setting_is_applied(self, settings_file):
        import admin_module
        import auth_service
        admin_module._write_setting("langue", "fr")
        assert auth_service._language() == "fr"

    def test_falls_back_to_former_env_var(self, settings_file, tmp_path):
        """Une installation existante a LANGUAGE dans son .env."""
        import auth_service
        (tmp_path / ".env").write_text("LANGUAGE=fr\n", encoding="utf-8")
        assert auth_service._language() == "fr"

    def test_unknown_language_ignored(self, settings_file):
        import admin_module
        import auth_service
        admin_module._write_setting("langue", "klingon")
        assert auth_service._language() == "en"

    def test_translations_follow_language(self, settings_file):
        """The point that matters: no more table frozen at startup."""
        import admin_module
        import auth_service

        admin_module._write_setting("langue", "fr")
        fr = auth_service.translations()["ui"]["invalid_token"]

        admin_module._write_setting("langue", "en")
        en = auth_service.translations()["ui"]["invalid_token"]

        assert fr != en, (fr, en)

    def test_ui_messages_follow_too(self, settings_file):
        """ui_messages() etait un dict construit une fois pour toutes."""
        import admin_module
        import auth_service

        admin_module._write_setting("langue", "fr")
        fr = auth_service.ui_messages()["INVALID_TOKEN"]
        admin_module._write_setting("langue", "en")
        en = auth_service.ui_messages()["INVALID_TOKEN"]

        assert fr != en, (fr, en)


# ============================================================================
# Rollback when Orthanc does not restart
# ============================================================================

class TestRollback:
    """A configuration can be valid and still be refused by Orthanc.

    Type and syntax say nothing about acceptability: DicomPort = 99999 is an
    integer, produces perfect JSON, and stops Orthanc from starting. Without
    a rollback, the panel leaves a PACS down while pointing the operator at
    the logs.

    The test that existed for this case already passed before the rollback
    did: with no backup available, we fall onto another path that also
    answers 504. Hence the cases below, which place one.
    """

    @staticmethod
    def _admin():
        from admin_module import AdminUser
        return AdminUser(username="admin", groups=["admin"])

    @pytest.fixture
    def stack(self, tmp_path, monkeypatch):
        """Un orthanc.json, une sauvegarde anterieure, et pas d'attente."""
        import admin_module

        config = tmp_path / "orthanc.json"
        config.write_text('{"Name": "casse"}', encoding="utf-8")

        sauvegardes = tmp_path / "backups"
        sauvegardes.mkdir()
        (sauvegardes / "orthanc.json.bak.20260101-120000").write_text(
            '{"Name": "connue-bonne"}', encoding="utf-8")

        monkeypatch.setattr(admin_module, "ORTHANC_JSON", config)
        monkeypatch.setattr(admin_module, "BACKUPS_DIR", sauvegardes)
        monkeypatch.setattr(admin_module, "DOCKER_PROXY_URL", "http://proxy:2375")

        async def _muet(*a, **k):
            return None

        async def _sans_attente(_):
            return None

        monkeypatch.setattr(admin_module, "_audit", _muet)
        monkeypatch.setattr(admin_module.asyncio, "sleep", _sans_attente)

        class _Reponse:
            status_code = 204

        class _Client:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **k): return _Reponse()

        monkeypatch.setattr(admin_module.httpx, "AsyncClient", _Client)
        return config

    @staticmethod
    def _orthanc_silent(monkeypatch):
        import admin_module

        async def _jamais(*a, **k):
            raise ConnectionError("Orthanc ne repond pas")

        monkeypatch.setattr(admin_module, "_orthanc", _jamais)

    @staticmethod
    def _orthanc_back_after_restore(monkeypatch, config: Path):
        """Orthanc only answers once the configuration has been restored."""
        import admin_module

        class _Reponse:
            status_code = 200

            @staticmethod
            def json():
                return {"Version": "1.12.11"}

        async def _selon_config(*a, **k):
            if "connue-bonne" in config.read_text(encoding="utf-8"):
                return _Reponse()
            raise ConnectionError("configuration refusee")

        monkeypatch.setattr(admin_module, "_orthanc", _selon_config)

    def test_configuration_restored_and_orthanc_restarts(self, stack, monkeypatch):
        """The case that matters: the PACS must come back, not stay down."""
        import admin_module
        from fastapi import HTTPException

        self._orthanc_back_after_restore(monkeypatch, stack)

        with pytest.raises(HTTPException) as e:
            _run(admin_module.restart_orthanc(self._admin()))

        # 500 and not 200: the requested change was NOT applied.
        assert e.value.status_code == 500
        assert "restauree" in e.value.detail
        assert "connue-bonne" in stack.read_text(encoding="utf-8")

    def test_restore_is_not_enough(self, stack, monkeypatch):
        """If Orthanc stays mute even after restoring, the cause lies elsewhere:
        say so rather than suggest a failed rollback."""
        import admin_module
        from fastapi import HTTPException

        self._orthanc_silent(monkeypatch)

        with pytest.raises(HTTPException) as e:
            _run(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 504
        assert "ailleurs" in e.value.detail

    def test_no_backup_available(self, stack, monkeypatch, tmp_path):
        import admin_module
        from fastapi import HTTPException

        vide = tmp_path / "vides"
        vide.mkdir()
        monkeypatch.setattr(admin_module, "BACKUPS_DIR", vide)
        self._orthanc_silent(monkeypatch)

        with pytest.raises(HTTPException) as e:
            _run(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 504
        assert "aucune sauvegarde" in e.value.detail

    def test_most_recent_is_chosen(self, stack, tmp_path):
        """Les noms portent un horodatage : l'ordre alphabetique fait foi."""
        import admin_module

        dossier = admin_module.BACKUPS_DIR
        for horodatage in ("20260101-120000", "20260301-090000",
                           "20260201-235959"):
            (dossier / f"orthanc.json.bak.{horodatage}").touch()
        choisie = admin_module._latest_orthanc_backup()
        assert choisie.name.endswith("20260301-090000")


# ============================================================================
# Contraintes de valeur
# ============================================================================

class TestRangesAndValues:
    """The type is not enough: an integer can be a port that does not exist."""

    @staticmethod
    def _change(champ, valeur):
        from admin_module import _apply_scalar_change
        config = {"DicomModalitiesInDatabase": True, "OrthancPeersInDatabase": True}
        _apply_scalar_change(config, champ, valeur)
        return config

    def test_port_out_of_range(self):
        with pytest.raises(ValueError, match="entre 1 et 65535"):
            self._change("DicomPort", 99999)

    def test_port_zero(self):
        with pytest.raises(ValueError, match="entre 1 et 65535"):
            self._change("DicomPort", 0)

    def test_valid_port(self):
        assert self._change("DicomPort", 11112)["DicomPort"] == 11112

    def test_zero_worker_threads(self):
        """Orthanc ne traiterait plus rien."""
        with pytest.raises(ValueError, match="entre 1 et 256"):
            self._change("ConcurrentJobs", 0)

    def test_negative_delay(self):
        with pytest.raises(ValueError, match="entre 0"):
            self._change("StableAge", -1)

    def test_value_outside_list(self):
        with pytest.raises(ValueError, match="default, verbose, trace"):
            self._change("LogLevel", "hurlant")

    def test_value_from_list(self):
        assert self._change("LogLevel", "verbose")["LogLevel"] == "verbose"

    def test_boolean_refused_on_integer_field(self):
        """En Python True vaut 1 : sans garde, il passerait pour un port."""
        with pytest.raises(ValueError, match="attendu int"):
            self._change("DicomPort", True)


# ============================================================================
# Explorer settings exposed in the panel
# ============================================================================

class TestExplorerSettings:
    """What must be settable without opening a file, and what must not be
    settable at all.

    The project targets an operator who does not hand-edit JSON. Appearance
    and sharing settings therefore belong in the panel. But two fields are
    deliberately absent, and that is what the second half of these tests
    locks down: exposing them would allow disabling the interface FROM the
    interface, with no way back other than editing the file -- precisely
    what we want to avoid.
    """

    @staticmethod
    def _change(champ, valeur):
        from admin_module import _apply_scalar_change
        config = {"DicomModalitiesInDatabase": True, "OrthancPeersInDatabase": True}
        _apply_scalar_change(config, champ, valeur)
        return config

    @pytest.mark.parametrize("champ", [
        "OrthancExplorer2.Theme",
        "OrthancExplorer2.Tokens.ShareType",
        "OrthancExplorer2.Tokens.InstantLinksValidity",
        "OrthancExplorer2.UiOptions.DefaultShareDuration",
        "OrthancExplorer2.UiOptions.EnableShares",
        "OrthancExplorer2.UiOptions.ShowOrthancName",
        "OrthancExplorer2.UiOptions.EnableOpenInOhifViewer3",
        "OrthancExplorer2.UiOptions.EnableOpenInStoneWebViewer",
        "OrthancExplorer2.UiOptions.EnableOpenInVolView",
    ])
    def test_settable_without_editing_the_file(self, champ):
        from admin_module import ORTHANC_EDITABLE_PATHS
        assert champ in ORTHANC_EDITABLE_PATHS

    @pytest.mark.parametrize("champ", [
        # Se couper l'acces a sa propre interface.
        "OrthancExplorer2.Enable",
        "OrthancExplorer2.IsDefaultOrthancUI",
        # Paths served by nginx: changing them breaks the links.
        "OrthancExplorer2.UiOptions.OhifViewer3PublicRoot",
        "OrthancExplorer2.UiOptions.StoneWebViewerPublicRoot",
        "OrthancExplorer2.UiOptions.VolViewPublicRoot",
        # Plomberie d'authentification.
        "Authorization.WebServiceUsername",
        "Authorization.WebServicePassword",
        "AuthenticationEnabled",
    ])
    def test_out_of_the_panel_reach(self, champ):
        from admin_module import ORTHANC_EDITABLE_PATHS
        assert champ not in ORTHANC_EDITABLE_PATHS

    def test_theme_limited_to_bootstrap_modes(self):
        """Explorer applies the value to data-bs-theme, which only knows light
        and dark."""
        with pytest.raises(ValueError, match="light, dark"):
            self._change("OrthancExplorer2.Theme", "fluo")

    def test_valid_theme(self):
        config = self._change("OrthancExplorer2.Theme", "light")
        assert config["OrthancExplorer2"]["Theme"] == "light"

    def test_share_duration_bounded(self):
        with pytest.raises(ValueError, match="entre 0 et 3650"):
            self._change("OrthancExplorer2.UiOptions.DefaultShareDuration", 9999)

    def test_share_without_expiry_allowed(self):
        """Zero is a legitimate value: a link with no end date."""
        config = self._change("OrthancExplorer2.UiOptions.DefaultShareDuration", 0)
        assert config["OrthancExplorer2"]["UiOptions"]["DefaultShareDuration"] == 0

    def test_every_exposed_field_has_a_label(self):
        """A field without a label shows up under its technical name, which
        teaches nothing to someone who does not write JSON."""
        from admin_module import ORTHANC_EDITABLE_PATHS
        from pathlib import Path as _P
        import re

        descriptions = (_P(__file__).resolve().parents[2]
                        / "frontend" / "src" / "orthanc_fields.js")
        if not descriptions.exists():
            pytest.skip("descriptions de champs hors de l'arborescence")

        texte = descriptions.read_text(encoding="utf-8")
        decrits = set(re.findall(r"'([A-Za-z][A-Za-z0-9.]*)':", texte))
        decrits |= set(re.findall(r"^\s+([A-Za-z][A-Za-z0-9]*): \[", texte, re.M))

        oublies = [c for c in ORTHANC_EDITABLE_PATHS
                   if c.startswith("OrthancExplorer2") and c not in decrits]
        assert not oublies, f"champs sans libelle : {oublies}"


# ============================================================================
# Reglages de type liste
# ============================================================================

class TestListSettings:
    """Displayed columns, viewer ordering, share durations.

    The scanner only recorded scalar values. A path pointing at an array was
    therefore seen as absent and went down the "insertion" branch, although
    the key existed: the file ended up with the same key TWICE. The
    read-back comparison did not catch it, json.loads keeping only the last
    one -- so the file stayed functional but ambiguous, and a parser keeping
    the first would have applied the previous configuration.
    """

    @staticmethod
    def _read_back(texte):
        from admin_module import _strip_json_comments
        return json.loads(_strip_json_comments(texte))

    @staticmethod
    def _change(champ, valeur):
        from admin_module import _apply_scalar_change
        config = {"DicomModalitiesInDatabase": True, "OrthancPeersInDatabase": True}
        _apply_scalar_change(config, champ, valeur)
        return config

    SOURCE = """{
  // Columns of the study list
  "StudyListColumns": ["PatientID", "PatientName"],
  "Theme": "dark"
}"""

    def test_array_is_located(self):
        from admin_module import _scan_json
        valeurs, _ = _scan_json(self.SOURCE)
        assert "StudyListColumns" in valeurs

    def test_replacement_without_duplicate(self):
        """The central point: a single occurrence of the key after writing."""
        from admin_module import _apply_text_changes
        out = _apply_text_changes(self.SOURCE, {"StudyListColumns": ["Modality"]})
        assert out.count('"StudyListColumns"') == 1
        assert self._read_back(out)["StudyListColumns"] == ["Modality"]

    def test_comment_preserved(self):
        from admin_module import _apply_text_changes
        out = _apply_text_changes(self.SOURCE, {"StudyListColumns": ["Modality"]})
        assert "// Columns of the study list" in out

    def test_neighbours_untouched(self):
        from admin_module import _apply_text_changes
        out = _apply_text_changes(self.SOURCE, {"StudyListColumns": ["Modality"]})
        assert self._read_back(out)["Theme"] == "dark"

    def test_readable_formatting(self):
        """A dozen entries on a single line would be unreadable in a file people
        reread to understand it."""
        from admin_module import _apply_text_changes
        out = _apply_text_changes(
            self.SOURCE, {"StudyListColumns": ["PatientID", "Modality"]})
        lignes = [l for l in out.splitlines() if '"Modality"' in l]
        assert lignes and lignes[0].startswith("    "), out

    def test_empty_list(self):
        from admin_module import _apply_text_changes
        out = _apply_text_changes(self.SOURCE, {"StudyListColumns": []})
        assert self._read_back(out)["StudyListColumns"] == []

    def test_key_present_but_not_locatable(self):
        """General guard: any type the analysis cannot handle must be refused,
        never inserted twice."""
        from admin_module import _apply_text_changes
        source = '{"Bloc": {"a": 1}}'
        with pytest.raises(ValueError, match="deja present"):
            _apply_text_changes(source, {"Bloc": {"a": 2}})

    # --- validation du contenu ---------------------------------------------

    def test_element_of_wrong_type(self):
        with pytest.raises(ValueError, match="type int"):
            self._change("OrthancExplorer2.UiOptions.ShareDurations", [7, "trente"])

    def test_negative_duration(self):
        with pytest.raises(ValueError, match="negative"):
            self._change("OrthancExplorer2.UiOptions.ShareDurations", [-5])

    def test_duplicates_refused(self):
        with pytest.raises(ValueError, match="doublons"):
            self._change("OrthancExplorer2.UiOptions.StudyListColumns",
                          ["PatientID", "PatientID"])

    def test_empty_entry_refused(self):
        with pytest.raises(ValueError, match="entree vide"):
            self._change("OrthancExplorer2.UiOptions.StudyListColumns", ["PatientID", "  "])

    def test_boolean_in_an_integer_list(self):
        """True equals 1 in Python: without a guard it would pass for a duration."""
        with pytest.raises(ValueError, match="type int"):
            self._change("OrthancExplorer2.UiOptions.ShareDurations", [True])

    def test_valid_list(self):
        config = self._change("OrthancExplorer2.UiOptions.ShareDurations",
                               [0, 7, 30])
        assert config["OrthancExplorer2"]["UiOptions"]["ShareDurations"] == [0, 7, 30]

    def test_scalar_refused_on_list_field(self):
        with pytest.raises(ValueError, match="attendu list"):
            self._change("OrthancExplorer2.UiOptions.StudyListColumns", "PatientID")


# ============================================================================
# The wizard does not reopen on a live installation
# ============================================================================

class TestSetupLock:
    """Redis is a cache: wiping it must not reopen the installation.

    The flag used to live there alone. A deleted volume, a migration, a
    docker volume prune, and the wizard reopened on a live PACS -- where
    anyone could then create themselves an administrator account.

    So we cross-check against a persistent truth: the existence of an active
    administrator other than the bootstrap account.
    """

    @pytest.fixture
    def without_redis(self, tmp_path, monkeypatch):
        """Redis vide, comme apres la perte de son volume."""
        import admin_module

        class _RedisVide:
            @staticmethod
            async def get(_cle):
                return None

        monkeypatch.setattr(admin_module, "_r", lambda: _RedisVide())

        fichier = tmp_path / "users_database.yml"
        monkeypatch.setattr(admin_module, "AUTHELIA_YML", fichier)
        return fichier

    @staticmethod
    def _write(fichier, users):
        import yaml
        fichier.write_text(yaml.safe_dump({"users": users}), encoding="utf-8")

    def test_first_run_stays_open(self, without_redis):
        """Seul le compte d'amorcage existe : c'est bien une installation
        neuve, l'assistant doit s'ouvrir."""
        import admin_module
        self._write(without_redis, {
            "bootstrap@localhost": {"disabled": False, "groups": ["admin"]},
        })
        assert _run(admin_module._setup_completed()) is False

    def test_existing_admin_locks(self, without_redis):
        """The case that matters: Redis empty, yet a real administrator exists.
        The wizard must stay closed."""
        import admin_module
        self._write(without_redis, {
            "gregory.cuffel": {"disabled": False, "groups": ["admin"]},
        })
        assert _run(admin_module._setup_completed()) is True

    def test_disabled_admin_does_not_lock(self, without_redis):
        """A disabled account cannot administer: the installation is then truly
        unusable, and the wizard has its place."""
        import admin_module
        self._write(without_redis, {
            "ancien.admin": {"disabled": True, "groups": ["admin"]},
        })
        assert _run(admin_module._setup_completed()) is False

    def test_plain_user_does_not_lock(self, without_redis):
        import admin_module
        self._write(without_redis, {
            "medecin": {"disabled": False, "groups": ["doctors"]},
        })
        assert _run(admin_module._setup_completed()) is False

    def test_unreadable_file_locks(self, without_redis):
        """When in doubt, do not open: a read error must not offer the creation
        of an administrator account."""
        import admin_module
        without_redis.write_text("ceci: n'est pas: du YAML: valide:", encoding="utf-8")
        assert _run(admin_module._setup_completed()) is True

    def test_redis_flag_is_enough(self, tmp_path, monkeypatch):
        """L'ancien mecanisme reste valable quand Redis repond."""
        import admin_module

        class _RedisPlein:
            @staticmethod
            async def get(_cle):
                return "1"

        monkeypatch.setattr(admin_module, "_r", lambda: _RedisPlein())
        monkeypatch.setattr(admin_module, "AUTHELIA_YML",
                            tmp_path / "absent.yml")
        assert _run(admin_module._setup_completed()) is True


# ============================================================================
# Divergence between what is written and what Orthanc applies
# ============================================================================

class TestEffectiveConfig:
    """Writing a value does not prove Orthanc applies it.

    Three ways to diverge with nothing to signal it: an ORTHANC__* variable
    from the compose file overriding the file, a field declared at the wrong
    place in the tree, a restart never performed.

    The second case is not theoretical: StudyListColumns lived under
    OrthancExplorer2 while Explorer reads it under UiOptions. The setting had
    never had any effect since it existed, and this check is what found
    it.
    """

    @pytest.fixture
    def config(self, tmp_path, monkeypatch):
        import admin_module

        fichier = tmp_path / "orthanc.json"
        fichier.write_text(json.dumps({
            "Name": "PACS Cuffel",
            "DicomAet": "PACSCUFFEL",
            "OrthancExplorer2": {"UiOptions": {"StudyListColumns": ["PatientID"]}},
        }), encoding="utf-8")
        monkeypatch.setattr(admin_module, "ORTHANC_JSON", fichier)
        return fichier

    @staticmethod
    def _respond(monkeypatch, systeme=None, ui=None):
        import admin_module

        class _Reponse:
            def __init__(self, corps):
                self.status_code = 200 if corps is not None else 500
                self._corps = corps or {}

            def json(self):
                return self._corps

        async def _faux(_methode, chemin, **_k):
            return _Reponse(systeme if chemin == "/system" else ui)

        monkeypatch.setattr(admin_module, "_orthanc", _faux)

    def test_no_divergence(self, config, monkeypatch):
        import admin_module
        self._respond(
            monkeypatch,
            systeme={"Name": "PACS Cuffel", "DicomAet": "PACSCUFFEL"},
            ui={"UiOptions": {"StudyListColumns": ["PatientID"]}},
        )
        assert _run(admin_module._check_effective_config()) == []

    def test_divergence_detected(self, config, monkeypatch):
        """Le cas d'une variable d'environnement qui ecrase le fichier."""
        import admin_module
        self._respond(
            monkeypatch,
            systeme={"Name": "Autre nom", "DicomAet": "PACSCUFFEL"},
            ui={"UiOptions": {"StudyListColumns": ["PatientID"]}},
        )
        ecarts = _run(admin_module._check_effective_config())
        assert len(ecarts) == 1
        assert ecarts[0]["champ"] == "Name"
        assert ecarts[0]["dans_le_fichier"] == "PACS Cuffel"
        assert ecarts[0]["applique_par_orthanc"] == "Autre nom"

    def test_misplaced_field_detected(self, config, monkeypatch):
        """The real defect: Orthanc applies its default columns because the field
        sits elsewhere in the tree."""
        import admin_module
        self._respond(
            monkeypatch,
            systeme={"Name": "PACS Cuffel", "DicomAet": "PACSCUFFEL"},
            ui={"UiOptions": {"StudyListColumns": ["PatientBirthDate", "modalities"]}},
        )
        ecarts = _run(admin_module._check_effective_config())
        champs = [e["champ"] for e in ecarts]
        assert "OrthancExplorer2.UiOptions.StudyListColumns" in champs

    def test_field_absent_from_file_ignored(self, tmp_path, monkeypatch):
        """Non declare = valeur par defaut d'Orthanc : ce n'est pas un ecart."""
        import admin_module
        fichier = tmp_path / "orthanc.json"
        fichier.write_text('{"Name": "PACS"}', encoding="utf-8")
        monkeypatch.setattr(admin_module, "ORTHANC_JSON", fichier)
        self._respond(monkeypatch, systeme={"Name": "PACS", "DicomPort": 4242},
                       ui={})
        assert _run(admin_module._check_effective_config()) == []

    def test_orthanc_silent(self, config, monkeypatch):
        """Nothing to compare must not translate into an alert."""
        import admin_module

        async def _casse(*a, **k):
            raise ConnectionError("Orthanc ne repond pas")

        monkeypatch.setattr(admin_module, "_orthanc", _casse)
        assert _run(admin_module._check_effective_config()) == []

    def test_computed_permissions_excluded(self):
        """EnableShares is true for an administrator and false for an external
        user: it is a permission, not a setting. Comparing it with the file
        would raise a permanent alert."""
        from admin_module import ORTHANC_VERIFIABLE
        for champ in ("OrthancExplorer2.UiOptions.EnableShares",
                      "OrthancExplorer2.UiOptions.EnableViewerQuickButton"):
            assert champ not in ORTHANC_VERIFIABLE

    def test_columns_declared_under_uioptions(self):
        """Placement guard: Explorer reads this field under UiOptions. Declaring
        it elsewhere would give back a silently ineffective setting."""
        from admin_module import ORTHANC_EDITABLE_PATHS
        assert "OrthancExplorer2.UiOptions.StudyListColumns" in ORTHANC_EDITABLE_PATHS
        assert "OrthancExplorer2.StudyListColumns" not in ORTHANC_EDITABLE_PATHS
