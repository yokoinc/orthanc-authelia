"""
Tests unitaires pour admin_module.py.

Focus sur les invariants qui protegent contre le lockout / la corruption :
  - _validate_authelia refuse un YAML sans admin actif
  - _apply_scalar_change refuse d'ecraser un dict/array
  - _validate_orthanc refuse la desactivation des flags *InDatabase
  - argon2 round-trip hash + verify

Executer avec :
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
# argon2 round-trip (bibliotheque tierce mais verifions notre wiring)
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
        # Simule ce que fait issue_csrf_cookie
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
# Commentaires JSON : Orthanc les accepte, json.loads non
# ============================================================================

class TestStripJsonComments:

    def test_line_comment_removed(self):
        from admin_module import _strip_json_comments
        import json
        raw = """{
  // un commentaire
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
        """Le // d'une URL ne doit pas etre pris pour un commentaire."""
        from admin_module import _strip_json_comments
        import json
        raw = '{"url": "http://auth-service:8000"}'
        out = json.loads(_strip_json_comments(raw))
        assert out["url"] == "http://auth-service:8000"

    def test_slashes_inside_string_preserved(self):
        from admin_module import _strip_json_comments
        import json
        raw = '{"path": "a//b", "glob": "/* pas un commentaire */"}'
        out = json.loads(_strip_json_comments(raw))
        assert out["path"] == "a//b"
        assert out["glob"] == "/* pas un commentaire */"

    def test_escaped_quote_inside_string(self):
        """Une quote echappee ne doit pas terminer la chaine prematurement."""
        from admin_module import _strip_json_comments
        import json
        raw = '{"quoted": "il a dit \\"bonjour\\"", "n": 1}'
        out = json.loads(_strip_json_comments(raw))
        assert out["n"] == 1

    def test_real_orthanc_config_shape(self):
        """Cas reel : commentaires en tete et URL avec // dans la meme config."""
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
# restart_orthanc : redemarrage via le proxy Docker
# ============================================================================

class TestRestartOrthanc:
    """La route qui redemarre Orthanc depuis le panel.

    Elle appelle un proxy qui n'expose que /containers/<id>/restart. Ce qui
    compte ici : ne jamais annoncer un succes sans qu'Orthanc ait reellement
    repondu -- une configuration acceptee a l'ecriture peut tres bien
    l'empecher de redemarrer, et l'exploitant doit l'apprendre tout de suite.
    """

    @staticmethod
    def _admin():
        from admin_module import AdminUser
        return AdminUser(username="admin", groups=["admin"])

    @pytest.fixture(autouse=True)
    def _sans_effets_de_bord(self, monkeypatch):
        """Neutralise l'audit (Redis) et les attentes entre deux sondages."""
        import admin_module

        async def _audit_muet(*a, **k):
            return None

        async def _sans_attente(_):
            return None

        monkeypatch.setattr(admin_module, "_audit", _audit_muet)
        monkeypatch.setattr(admin_module.asyncio, "sleep", _sans_attente)

    def test_proxy_non_configure_repond_503(self, monkeypatch):
        """Sans DOCKER_PROXY_URL la fonction est indisponible, pas cassee."""
        import admin_module
        from fastapi import HTTPException

        monkeypatch.setattr(admin_module, "DOCKER_PROXY_URL", "")

        with pytest.raises(HTTPException) as e:
            _executer(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 503
        assert "DOCKER_PROXY_URL" in e.value.detail

    def test_conteneur_introuvable(self, monkeypatch):
        """404 du proxy = mauvais nom de conteneur : le dire explicitement."""
        import admin_module
        from fastapi import HTTPException

        self._brancher_proxy(monkeypatch, 404)

        with pytest.raises(HTTPException) as e:
            _executer(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 502
        assert "ORTHANC_CONTAINER" in e.value.detail

    def test_redemarrage_refuse_par_le_proxy(self, monkeypatch):
        """403 = ALLOW_RESTARTS absent. Orienter vers la bonne cause."""
        import admin_module
        from fastapi import HTTPException

        self._brancher_proxy(monkeypatch, 403)

        with pytest.raises(HTTPException) as e:
            _executer(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 502
        assert "ALLOW_RESTARTS" in e.value.detail

    def test_succes_quand_orthanc_repond(self, monkeypatch):
        """Le cas nominal : 204 du proxy puis /system qui repond 200."""
        import admin_module

        self._brancher_proxy(monkeypatch, 204)
        self._brancher_system(monkeypatch, [200])

        r = _executer(admin_module.restart_orthanc(self._admin()))
        assert r["ok"] is True
        assert r["version"] == "1.12.11"

    def test_succes_apres_quelques_sondages(self, monkeypatch):
        """Orthanc ouvre son port avant d'etre pret : on attend qu'il reponde."""
        import admin_module

        self._brancher_proxy(monkeypatch, 204)
        self._brancher_system(monkeypatch, [502, 502, 200])

        r = _executer(admin_module.restart_orthanc(self._admin()))
        assert r["ok"] is True

    def test_orthanc_ne_revient_pas(self, monkeypatch):
        """Le point important : pas de faux succes si Orthanc reste muet."""
        import admin_module
        from fastapi import HTTPException

        self._brancher_proxy(monkeypatch, 204)
        self._brancher_system(monkeypatch, [502] * 40)

        with pytest.raises(HTTPException) as e:
            _executer(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 504
        assert "journaux" in e.value.detail

    # --- outillage ---------------------------------------------------------

    @staticmethod
    def _brancher_proxy(monkeypatch, code: int):
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
    def _brancher_system(monkeypatch, codes: list):
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


def _executer(coro):
    """Execute une coroutine dans une boucle neuve, fermee ensuite."""
    boucle = asyncio.new_event_loop()
    try:
        return boucle.run_until_complete(coro)
    finally:
        boucle.close()


# ============================================================================
# Ecriture d'orthanc.json : preserver ce que la structure ne porte pas
# ============================================================================

class TestEcritureNonDestructive:
    """Le panel edite le texte plutot que de regenerer le fichier.

    Une reecriture par json.dumps() efface commentaires, ordre et
    groupements. Constate sur une installation reelle : la premiere
    modification faite depuis le panel avait supprime les 44 commentaires du
    fichier, soit l'essentiel de sa documentation.

    Les cas rassembles ici sont ceux ou une edition textuelle naive se
    trompe : un nom de cle cite dans un commentaire, une accolade dans une
    chaine, un commentaire colle a la valeur.
    """

    @staticmethod
    def _relire(texte):
        from admin_module import _strip_json_comments
        return json.loads(_strip_json_comments(texte))

    def test_commentaires_preserves(self):
        from admin_module import _apply_text_changes
        source = """{
  // Nom affiche dans l'interface
  "Name": "Orthanc",

  // Titre applicatif DICOM, 16 caracteres au plus
  "DicomAet": "ORTHANC"
}"""
        out = _apply_text_changes(source, {"Name": "PACS"})
        assert out.count("//") == 2
        assert "Nom affiche dans l'interface" in out
        assert self._relire(out) == {"Name": "PACS", "DicomAet": "ORTHANC"}

    def test_seule_la_ligne_visee_change(self):
        """Une modification ne doit pas reformater le reste du fichier."""
        from admin_module import _apply_text_changes
        source = '{\n  "A": 1,\n  "B": 2,\n  "C": 3\n}'
        out = _apply_text_changes(source, {"B": 20})
        avant, apres = source.splitlines(), out.splitlines()
        assert len(avant) == len(apres)
        assert [i for i, (a, b) in enumerate(zip(avant, apres)) if a != b] == [2]

    def test_cle_citee_dans_un_commentaire(self):
        """Le piege classique : le nom de la cle apparait aussi en commentaire."""
        from admin_module import _apply_text_changes
        source = """{
  // Ne pas confondre avec "Name" du bloc DicomWeb ci-dessous
  "Name": "Orthanc"
}"""
        out = _apply_text_changes(source, {"Name": "PACS"})
        assert 'avec "Name" du bloc' in out       # le commentaire est intact
        assert self._relire(out) == {"Name": "PACS"}

    def test_accolade_dans_une_chaine(self):
        """Une accolade entre guillemets ne doit pas etre lue comme un bloc."""
        from admin_module import _apply_text_changes
        source = '{\n  "Motif": "prefixe{suffixe}",\n  "Name": "Orthanc"\n}'
        out = _apply_text_changes(source, {"Name": "PACS"})
        assert self._relire(out) == {"Motif": "prefixe{suffixe}", "Name": "PACS"}

    def test_commentaire_en_fin_de_ligne(self):
        """La valeur s'arrete avant le //, qui doit survivre tel quel."""
        from admin_module import _apply_text_changes
        source = '{\n  "Taille": 500, // en megaoctets\n  "Name": "Orthanc"\n}'
        out = _apply_text_changes(source, {"Taille": 800})
        assert "// en megaoctets" in out
        assert self._relire(out)["Taille"] == 800

    def test_cle_imbriquee(self):
        from admin_module import _apply_text_changes
        source = """{
  "Name": "Orthanc",
  "DicomWeb": {
    // Taille maximale d'un envoi STOW-RS
    "StowMaxSize": 500,
    "Enable": true
  }
}"""
        out = _apply_text_changes(source, {"DicomWeb.StowMaxSize": 1000})
        assert "Taille maximale" in out
        assert self._relire(out)["DicomWeb"] == {"StowMaxSize": 1000, "Enable": True}

    def test_cle_absente_ajoutee(self):
        """Orthanc laisse beaucoup de reglages implicites : les definir est un
        cas courant, pas une exception."""
        from admin_module import _apply_text_changes
        source = '{\n  // Reglages de base\n  "Name": "Orthanc"\n}'
        out = _apply_text_changes(source, {"DicomAlwaysAllowStore": False})
        assert "// Reglages de base" in out
        assert self._relire(out) == {"Name": "Orthanc", "DicomAlwaysAllowStore": False}
        # Meme indentation que ses voisines : une cle decalee se remarque, et
        # donne l'impression d'un fichier edite a la main a la va-vite.
        ligne = [l for l in out.splitlines() if "DicomAlwaysAllowStore" in l][0]
        assert ligne.startswith('  "'), repr(ligne)

    def test_cle_ajoutee_apres_un_commentaire_final(self):
        """Le commentaire de fin de bloc doit rester en dernier."""
        from admin_module import _apply_text_changes
        source = '{\n  "Name": "Orthanc"\n  // fin du bloc\n}'
        out = _apply_text_changes(source, {"DicomAet": "PACS"})
        assert self._relire(out) == {"Name": "Orthanc", "DicomAet": "PACS"}
        assert out.index('"DicomAet"') < out.index("// fin du bloc")

    def test_ajout_dans_un_objet_imbrique(self):
        from admin_module import _apply_text_changes
        source = '{\n  "DicomWeb": {\n    "Enable": true\n  }\n}'
        out = _apply_text_changes(source, {"DicomWeb.StowMaxSize": 500})
        assert self._relire(out)["DicomWeb"] == {"Enable": True, "StowMaxSize": 500}
        ligne = [l for l in out.splitlines() if "StowMaxSize" in l][0]
        assert ligne.startswith('    "'), repr(ligne)

    def test_parent_absent_refuse(self):
        """Creer une arborescence demanderait de deviner une mise en forme :
        on prefere le signaler et laisser l'appelant regenerer."""
        from admin_module import _apply_text_changes
        with pytest.raises(ValueError, match="parent absent"):
            _apply_text_changes('{\n  "Name": "Orthanc"\n}',
                                {"Absent.Cle": 1})

    def test_plusieurs_changements_a_la_fois(self):
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
        assert self._relire(out) == {
            "Name": "PACS", "DicomAet": "ORTHANC", "DicomPort": 11112,
            "DicomCheckCalledAet": True,
        }

    def test_types_scalaires(self):
        """booleen, entier, chaine et null doivent se relire a l'identique."""
        from admin_module import _apply_text_changes
        source = '{\n  "A": 1,\n  "B": "x",\n  "C": true,\n  "D": null\n}'
        out = _apply_text_changes(source, {"A": 42, "B": "y", "C": False, "D": "z"})
        assert self._relire(out) == {"A": 42, "B": "y", "C": False, "D": "z"}

    def test_fichier_reel_du_depot(self):
        """Le fichier livre : aucun commentaire ne doit disparaitre.

        Le fichier est cherche en remontant l'arborescence, et non a une
        profondeur fixe : selon qu'on monte le depot entier ou le seul
        dossier sources/, le nombre de niveaux differe. Un index fige a fait
        echouer la CI alors que la suite passait en local.
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
        assert self._relire(out)["Name"] == "PACS Cuffel"


# ============================================================================
# Viewer par defaut des liens de partage
# ============================================================================

class TestViewerDePartage:
    """Le viewer preselectionne au moment de partager un examen.

    Ces tests ont ete refaits : les precedents verifiaient qu'une valeur
    ecrite dans les reglages etait bien relue, sans jamais etablir que
    quelqu'un la consulte. Elle ne l'etait pas -- Explorer lit
    OrthancExplorer2.Tokens.ShareType dans orthanc.json, et son bundle ne
    contient aucune occurrence du "default-viewer" que renvoyait
    /settings/roles. Le reglage s'ecrivait, se relisait, et ne changeait rien
    a l'ecran.

    D'ou le garde-fou ci-dessous : le chemin vise doit rester celui
    qu'Explorer lit.
    """

    @pytest.fixture
    def config_orthanc(self, tmp_path, monkeypatch):
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

    def test_chemin_vise_est_celui_qu_explorer_lit(self):
        """Garde-fou : Explorer fait `tokenType: this.tokens.ShareType`.

        Si ce chemin disparait des champs modifiables, le reglage redevient
        sans effet -- en silence, car il continuerait de s'ecrire et de se
        relire correctement.
        """
        from admin_module import ORTHANC_EDITABLE_PATHS
        assert "OrthancExplorer2.Tokens.ShareType" in ORTHANC_EDITABLE_PATHS

    def test_lecture_depuis_orthanc_json(self, config_orthanc):
        from admin_module import _read_share_type
        assert _read_share_type() == "volview-viewer-publication"

    def test_valeur_inconnue_ignoree(self, config_orthanc):
        """Une valeur hors liste ne doit pas casser le menu de partage."""
        from admin_module import _read_share_type
        config_orthanc.write_text(
            '{"OrthancExplorer2": {"Tokens": {"ShareType": "nimporte-quoi"}}}',
            encoding="utf-8")
        assert _read_share_type() == "ohif-viewer-publication"

    def test_champ_absent(self, config_orthanc):
        from admin_module import _read_share_type
        config_orthanc.write_text('{"Name": "PACS"}', encoding="utf-8")
        assert _read_share_type() == "ohif-viewer-publication"

    def test_fichier_illisible(self, tmp_path, monkeypatch):
        import admin_module
        from admin_module import _read_share_type
        monkeypatch.setattr(admin_module, "ORTHANC_JSON",
                            tmp_path / "absent.json")
        assert _read_share_type() == "ohif-viewer-publication"

    def test_ecriture_preserve_les_commentaires(self, config_orthanc):
        """L'ecriture passe par la meme mecanique que le reste de la config."""
        from admin_module import _apply_text_changes, _strip_json_comments
        import json as _json

        source = config_orthanc.read_text(encoding="utf-8")
        out = _apply_text_changes(
            source,
            {"OrthancExplorer2.Tokens.ShareType": "ohif-viewer-publication"},
        )
        assert "// Interface web" in out
        relu = _json.loads(_strip_json_comments(out))
        assert relu["OrthancExplorer2"]["Tokens"]["ShareType"] == "ohif-viewer-publication"

    def test_auth_service_renvoie_la_meme_valeur(self, config_orthanc):
        """/settings/roles ne doit pas contredire ce qui s'applique."""
        import auth_service
        assert auth_service._default_share_viewer() == "volview-viewer-publication"


# ============================================================================
# Magasin de reglages applicatifs
# ============================================================================

class TestMagasinReglages:
    """Les reglages que seul le panel utilise vivent hors du .env.

    Le .env n'a de raison d'etre que pour ce que docker compose doit connaitre
    avant de demarrer un container. Y loger une preference d'interface oblige
    a le monter en ecriture, a le reecrire sur place, et melange des libelles
    avec des mots de passe.
    """

    @pytest.fixture
    def reglages(self, tmp_path, monkeypatch):
        import admin_module

        fichier = tmp_path / "app-settings" / "settings.json"
        monkeypatch.setattr(admin_module, "SETTINGS_FILE", fichier)
        monkeypatch.setattr(admin_module, "ENV_FILE", tmp_path / ".env")
        return fichier

    def test_ecriture_puis_lecture(self, reglages):
        from admin_module import _write_setting, _read_setting
        _write_setting("share_default_viewer", "stone-viewer-publication")
        assert _read_setting("share_default_viewer") == "stone-viewer-publication"

    def test_dossier_cree_au_besoin(self, reglages):
        """Une installation neuve n'a pas encore le fichier."""
        from admin_module import _write_setting
        assert not reglages.parent.exists()
        _write_setting("langue", "fr")
        assert reglages.exists()

    def test_plusieurs_reglages_coexistent(self, reglages):
        from admin_module import _write_setting, _read_setting
        _write_setting("a", 1)
        _write_setting("b", "deux")
        assert (_read_setting("a"), _read_setting("b")) == (1, "deux")

    def test_reprise_de_l_ancienne_variable(self, reglages, tmp_path):
        """Une installation existante a le reglage dans son .env : il doit
        continuer a s'appliquer tant qu'on ne l'a pas redefini."""
        from admin_module import _read_setting
        (tmp_path / ".env").write_text(
            "SHARE_DEFAULT_VIEWER=stone-viewer-publication\n", encoding="utf-8")
        assert _read_setting("share_default_viewer",
                             "SHARE_DEFAULT_VIEWER") == "stone-viewer-publication"

    def test_le_fichier_prime_sur_le_env(self, reglages, tmp_path):
        """Apres la premiere ecriture, la ligne du .env devient inerte."""
        from admin_module import _write_setting, _read_setting
        (tmp_path / ".env").write_text(
            "SHARE_DEFAULT_VIEWER=stone-viewer-publication\n", encoding="utf-8")
        _write_setting("share_default_viewer", "volview-viewer-publication")
        assert _read_setting("share_default_viewer",
                             "SHARE_DEFAULT_VIEWER") == "volview-viewer-publication"

    def test_fichier_illisible_ne_casse_rien(self, reglages):
        """Un JSON corrompu doit degrader vers les valeurs par defaut, pas
        empecher le service de repondre."""
        from admin_module import _read_setting
        reglages.parent.mkdir(parents=True)
        reglages.write_text("{ceci n'est pas du JSON", encoding="utf-8")
        assert _read_setting("share_default_viewer", default="ohif") == "ohif"

    def test_ecriture_atomique(self, reglages):
        """Aucun fichier temporaire ne doit subsister apres l'ecriture."""
        from admin_module import _write_setting
        _write_setting("a", 1)
        restes = [f.name for f in reglages.parent.iterdir()
                  if f.name != "settings.json"]
        assert restes == [], restes

    def test_aucun_secret_dans_le_fichier(self, reglages):
        """Garde-fou de conception : ce fichier n'est pas un coffre. Il vit
        dans data/, echappe au .gitignore des secrets, et pourrait etre
        recopie sans precaution."""
        from admin_module import _write_setting
        _write_setting("share_default_viewer", "ohif-viewer-publication")
        contenu = reglages.read_text(encoding="utf-8").lower()
        for interdit in ("password", "secret", "token", "_key"):
            assert interdit not in contenu, interdit


# ============================================================================
# Langue de l'interface
# ============================================================================

class TestLangue:
    """La langue etait figee au chargement du module, depuis le .env.

    En changer imposait de recreer le container, pour une preference
    d'affichage. Les translations sont desormais resolues a l'affichage, ce qui
    permet de la changer depuis le panel.
    """

    @pytest.fixture
    def reglages(self, tmp_path, monkeypatch):
        import admin_module
        import auth_service

        fichier = tmp_path / "app-settings" / "settings.json"
        monkeypatch.setattr(admin_module, "SETTINGS_FILE", fichier)
        monkeypatch.setattr(admin_module, "ENV_FILE", tmp_path / ".env")
        monkeypatch.delenv("LANGUAGE", raising=False)
        # Le cache de translations survit d'un test a l'autre.
        auth_service._translations_cache["langue"] = None
        return fichier

    def test_defaut_anglais(self, reglages):
        import auth_service
        assert auth_service._language() == "en"

    def test_reglage_pris_en_compte(self, reglages):
        import admin_module
        import auth_service
        admin_module._write_setting("langue", "fr")
        assert auth_service._language() == "fr"

    def test_reprise_de_l_ancienne_variable(self, reglages, tmp_path):
        """Une installation existante a LANGUAGE dans son .env."""
        import auth_service
        (tmp_path / ".env").write_text("LANGUAGE=fr\n", encoding="utf-8")
        assert auth_service._language() == "fr"

    def test_langue_inconnue_ignoree(self, reglages):
        import admin_module
        import auth_service
        admin_module._write_setting("langue", "klingon")
        assert auth_service._language() == "en"

    def test_traductions_suivent_la_langue(self, reglages):
        """Le point qui compte : plus de table figee au demarrage."""
        import admin_module
        import auth_service

        admin_module._write_setting("langue", "fr")
        fr = auth_service.translations()["ui"]["invalid_token"]

        admin_module._write_setting("langue", "en")
        en = auth_service.translations()["ui"]["invalid_token"]

        assert fr != en, (fr, en)

    def test_messages_ui_suivent_aussi(self, reglages):
        """ui_messages() etait un dict construit une fois pour toutes."""
        import admin_module
        import auth_service

        admin_module._write_setting("langue", "fr")
        fr = auth_service.ui_messages()["INVALID_TOKEN"]
        admin_module._write_setting("langue", "en")
        en = auth_service.ui_messages()["INVALID_TOKEN"]

        assert fr != en, (fr, en)


# ============================================================================
# Retour arriere quand Orthanc ne redemarre pas
# ============================================================================

class TestRetourArriere:
    """Une configuration peut etre valide et refusee par Orthanc.

    Le type et la syntaxe ne disent rien de l'acceptabilite : DicomPort =
    99999 est un entier, produit un JSON parfait, et empeche Orthanc de
    demarrer. Sans retour arriere, le panel laisse un PACS eteint en
    renvoyant l'exploitant vers les journaux.

    Le test qui existait pour ce cas passait deja avant que le retour arriere
    existe : sans sauvegarde disponible, on tombe sur un autre chemin qui
    repond aussi 504. D'ou les cas ci-dessous, qui en placent une.
    """

    @staticmethod
    def _admin():
        from admin_module import AdminUser
        return AdminUser(username="admin", groups=["admin"])

    @pytest.fixture
    def pile(self, tmp_path, monkeypatch):
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
    def _orthanc_muet(monkeypatch):
        import admin_module

        async def _jamais(*a, **k):
            raise ConnectionError("Orthanc ne repond pas")

        monkeypatch.setattr(admin_module, "_orthanc", _jamais)

    @staticmethod
    def _orthanc_revient_apres_restauration(monkeypatch, config: Path):
        """Orthanc ne repond que lorsque la configuration a ete restauree."""
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

    def test_configuration_restauree_et_orthanc_repart(self, pile, monkeypatch):
        """Le cas qui compte : le PACS doit revenir, pas rester eteint."""
        import admin_module
        from fastapi import HTTPException

        self._orthanc_revient_apres_restauration(monkeypatch, pile)

        with pytest.raises(HTTPException) as e:
            _executer(admin_module.restart_orthanc(self._admin()))

        # 500 et non 200 : la modification demandee n'a PAS ete appliquee.
        assert e.value.status_code == 500
        assert "restauree" in e.value.detail
        assert "connue-bonne" in pile.read_text(encoding="utf-8")

    def test_restauration_insuffisante(self, pile, monkeypatch):
        """Si Orthanc reste muet meme apres restauration, la cause est
        ailleurs : le dire plutot que de laisser croire a un rollback rate."""
        import admin_module
        from fastapi import HTTPException

        self._orthanc_muet(monkeypatch)

        with pytest.raises(HTTPException) as e:
            _executer(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 504
        assert "ailleurs" in e.value.detail

    def test_aucune_sauvegarde(self, pile, monkeypatch, tmp_path):
        import admin_module
        from fastapi import HTTPException

        vide = tmp_path / "vides"
        vide.mkdir()
        monkeypatch.setattr(admin_module, "BACKUPS_DIR", vide)
        self._orthanc_muet(monkeypatch)

        with pytest.raises(HTTPException) as e:
            _executer(admin_module.restart_orthanc(self._admin()))
        assert e.value.status_code == 504
        assert "aucune sauvegarde" in e.value.detail

    def test_la_plus_recente_est_choisie(self, pile, tmp_path):
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

class TestBornesEtValeurs:
    """Le type ne suffit pas : un entier peut etre un port qui n'existe pas."""

    @staticmethod
    def _changer(champ, valeur):
        from admin_module import _apply_scalar_change
        config = {"DicomModalitiesInDatabase": True, "OrthancPeersInDatabase": True}
        _apply_scalar_change(config, champ, valeur)
        return config

    def test_port_hors_bornes(self):
        with pytest.raises(ValueError, match="entre 1 et 65535"):
            self._changer("DicomPort", 99999)

    def test_port_zero(self):
        with pytest.raises(ValueError, match="entre 1 et 65535"):
            self._changer("DicomPort", 0)

    def test_port_valide(self):
        assert self._changer("DicomPort", 11112)["DicomPort"] == 11112

    def test_zero_fil_d_execution(self):
        """Orthanc ne traiterait plus rien."""
        with pytest.raises(ValueError, match="entre 1 et 256"):
            self._changer("ConcurrentJobs", 0)

    def test_delai_negatif(self):
        with pytest.raises(ValueError, match="entre 0"):
            self._changer("StableAge", -1)

    def test_valeur_hors_liste(self):
        with pytest.raises(ValueError, match="default, verbose, trace"):
            self._changer("LogLevel", "hurlant")

    def test_valeur_de_la_liste(self):
        assert self._changer("LogLevel", "verbose")["LogLevel"] == "verbose"

    def test_booleen_refuse_sur_un_champ_entier(self):
        """En Python True vaut 1 : sans garde, il passerait pour un port."""
        with pytest.raises(ValueError, match="attendu int"):
            self._changer("DicomPort", True)


# ============================================================================
# Reglages d'Explorer exposes dans le panel
# ============================================================================

class TestReglagesExplorer:
    """Ce qui doit etre reglable sans ouvrir un fichier, et ce qui ne doit
    pas l'etre du tout.

    Le projet vise un exploitant qui n'edite pas de JSON a la main. Les
    reglages d'apparence et de partage doivent donc etre dans le panel. Mais
    deux champs en sont volontairement absents, et c'est ce que verrouille la
    seconde moitie de ces tests : les exposer permettrait de desactiver
    l'interface DEPUIS l'interface, sans autre retour en arriere que d'editer
    le fichier -- precisement ce qu'on veut eviter.
    """

    @staticmethod
    def _changer(champ, valeur):
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
    def test_reglable_sans_editer_le_fichier(self, champ):
        from admin_module import ORTHANC_EDITABLE_PATHS
        assert champ in ORTHANC_EDITABLE_PATHS

    @pytest.mark.parametrize("champ", [
        # Se couper l'acces a sa propre interface.
        "OrthancExplorer2.Enable",
        "OrthancExplorer2.IsDefaultOrthancUI",
        # Chemins servis par nginx : les changer casse les liens.
        "OrthancExplorer2.UiOptions.OhifViewer3PublicRoot",
        "OrthancExplorer2.UiOptions.StoneWebViewerPublicRoot",
        "OrthancExplorer2.UiOptions.VolViewPublicRoot",
        # Plomberie d'authentification.
        "Authorization.WebServiceUsername",
        "Authorization.WebServicePassword",
        "AuthenticationEnabled",
    ])
    def test_hors_de_portee_du_panel(self, champ):
        from admin_module import ORTHANC_EDITABLE_PATHS
        assert champ not in ORTHANC_EDITABLE_PATHS

    def test_theme_limite_aux_modes_bootstrap(self):
        """Explorer applique la valeur a data-bs-theme, qui ne connait que
        clair et sombre."""
        with pytest.raises(ValueError, match="light, dark"):
            self._changer("OrthancExplorer2.Theme", "fluo")

    def test_theme_valide(self):
        config = self._changer("OrthancExplorer2.Theme", "light")
        assert config["OrthancExplorer2"]["Theme"] == "light"

    def test_duree_de_partage_bornee(self):
        with pytest.raises(ValueError, match="entre 0 et 3650"):
            self._changer("OrthancExplorer2.UiOptions.DefaultShareDuration", 9999)

    def test_partage_sans_expiration_autorise(self):
        """Zero est une valeur legitime : un lien sans date de fin."""
        config = self._changer("OrthancExplorer2.UiOptions.DefaultShareDuration", 0)
        assert config["OrthancExplorer2"]["UiOptions"]["DefaultShareDuration"] == 0

    def test_chaque_champ_expose_a_un_libelle(self):
        """Un champ sans libelle s'affiche sous son nom technique, ce qui
        n'apprend rien a qui n'ecrit pas de JSON."""
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

class TestListes:
    """Colonnes affichees, ordre des visionneuses, durees de partage.

    Le scanner ne relevait que les valeurs scalaires. Un chemin pointant sur
    un tableau etait donc vu comme absent et partait dans la branche
    « insertion », alors que la cle existait : le fichier se retrouvait avec
    DEUX fois la meme cle. La relecture-comparaison ne le voyait pas,
    json.loads ne retenant que la derniere -- le fichier restait donc
    fonctionnel mais ambigu, et un analyseur retenant la premiere aurait
    applique l'ancienne configuration.
    """

    @staticmethod
    def _relire(texte):
        from admin_module import _strip_json_comments
        return json.loads(_strip_json_comments(texte))

    @staticmethod
    def _changer(champ, valeur):
        from admin_module import _apply_scalar_change
        config = {"DicomModalitiesInDatabase": True, "OrthancPeersInDatabase": True}
        _apply_scalar_change(config, champ, valeur)
        return config

    SOURCE = """{
  // Colonnes de la liste d'examens
  "StudyListColumns": ["PatientID", "PatientName"],
  "Theme": "dark"
}"""

    def test_le_tableau_est_localise(self):
        from admin_module import _scan_json
        valeurs, _ = _scan_json(self.SOURCE)
        assert "StudyListColumns" in valeurs

    def test_remplacement_sans_doublon(self):
        """Le point central : une seule occurrence de la cle apres ecriture."""
        from admin_module import _apply_text_changes
        out = _apply_text_changes(self.SOURCE, {"StudyListColumns": ["Modality"]})
        assert out.count('"StudyListColumns"') == 1
        assert self._relire(out)["StudyListColumns"] == ["Modality"]

    def test_commentaire_preserve(self):
        from admin_module import _apply_text_changes
        out = _apply_text_changes(self.SOURCE, {"StudyListColumns": ["Modality"]})
        assert "// Colonnes de la liste d'examens" in out

    def test_voisins_intacts(self):
        from admin_module import _apply_text_changes
        out = _apply_text_changes(self.SOURCE, {"StudyListColumns": ["Modality"]})
        assert self._relire(out)["Theme"] == "dark"

    def test_mise_en_forme_lisible(self):
        """Une dizaine d'entrees sur une seule ligne serait illisible dans un
        fichier qu'on relit pour comprendre."""
        from admin_module import _apply_text_changes
        out = _apply_text_changes(
            self.SOURCE, {"StudyListColumns": ["PatientID", "Modality"]})
        lignes = [l for l in out.splitlines() if '"Modality"' in l]
        assert lignes and lignes[0].startswith("    "), out

    def test_liste_vide(self):
        from admin_module import _apply_text_changes
        out = _apply_text_changes(self.SOURCE, {"StudyListColumns": []})
        assert self._relire(out)["StudyListColumns"] == []

    def test_cle_presente_mais_non_localisable(self):
        """Garde-fou general : tout type que l'analyse ne sait pas traiter
        doit etre refuse, jamais insere en double."""
        from admin_module import _apply_text_changes
        source = '{"Bloc": {"a": 1}}'
        with pytest.raises(ValueError, match="deja present"):
            _apply_text_changes(source, {"Bloc": {"a": 2}})

    # --- validation du contenu ---------------------------------------------

    def test_element_de_mauvais_type(self):
        with pytest.raises(ValueError, match="type int"):
            self._changer("OrthancExplorer2.UiOptions.ShareDurations", [7, "trente"])

    def test_duree_negative(self):
        with pytest.raises(ValueError, match="negative"):
            self._changer("OrthancExplorer2.UiOptions.ShareDurations", [-5])

    def test_doublons_refuses(self):
        with pytest.raises(ValueError, match="doublons"):
            self._changer("OrthancExplorer2.UiOptions.StudyListColumns",
                          ["PatientID", "PatientID"])

    def test_entree_vide_refusee(self):
        with pytest.raises(ValueError, match="entree vide"):
            self._changer("OrthancExplorer2.UiOptions.StudyListColumns", ["PatientID", "  "])

    def test_booleen_dans_une_liste_d_entiers(self):
        """True vaut 1 en Python : sans garde, il passerait pour une duree."""
        with pytest.raises(ValueError, match="type int"):
            self._changer("OrthancExplorer2.UiOptions.ShareDurations", [True])

    def test_liste_valide(self):
        config = self._changer("OrthancExplorer2.UiOptions.ShareDurations",
                               [0, 7, 30])
        assert config["OrthancExplorer2"]["UiOptions"]["ShareDurations"] == [0, 7, 30]

    def test_scalaire_refuse_sur_un_champ_liste(self):
        with pytest.raises(ValueError, match="attendu list"):
            self._changer("OrthancExplorer2.UiOptions.StudyListColumns", "PatientID")


# ============================================================================
# Le wizard ne se rouvre pas sur une installation en service
# ============================================================================

class TestVerrouInstallation:
    """Redis est un cache : le vider ne doit pas rouvrir l'installation.

    Le drapeau y vivait seul. Un volume efface, une migration, un
    docker volume prune, et l'assistant se rouvrait sur un PACS en service --
    ou n'importe qui pouvait alors se creer un compte administrateur.

    On croise donc avec une verite persistante : l'existence d'un
    administrateur actif autre que le compte d'amorcage.
    """

    @pytest.fixture
    def sans_redis(self, tmp_path, monkeypatch):
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
    def _ecrire(fichier, users):
        import yaml
        fichier.write_text(yaml.safe_dump({"users": users}), encoding="utf-8")

    def test_premier_lancement_reste_ouvert(self, sans_redis):
        """Seul le compte d'amorcage existe : c'est bien une installation
        neuve, l'assistant doit s'ouvrir."""
        import admin_module
        self._ecrire(sans_redis, {
            "bootstrap@localhost": {"disabled": False, "groups": ["admin"]},
        })
        assert _executer(admin_module._setup_completed()) is False

    def test_admin_existant_verrouille(self, sans_redis):
        """Le cas qui compte : Redis vide, mais un administrateur reel
        existe. L'assistant doit rester ferme."""
        import admin_module
        self._ecrire(sans_redis, {
            "gregory.cuffel": {"disabled": False, "groups": ["admin"]},
        })
        assert _executer(admin_module._setup_completed()) is True

    def test_admin_desactive_ne_verrouille_pas(self, sans_redis):
        """Un compte desactive ne peut pas administrer : l'installation est
        alors reellement inutilisable, et l'assistant a sa place."""
        import admin_module
        self._ecrire(sans_redis, {
            "ancien.admin": {"disabled": True, "groups": ["admin"]},
        })
        assert _executer(admin_module._setup_completed()) is False

    def test_utilisateur_simple_ne_verrouille_pas(self, sans_redis):
        import admin_module
        self._ecrire(sans_redis, {
            "medecin": {"disabled": False, "groups": ["doctors"]},
        })
        assert _executer(admin_module._setup_completed()) is False

    def test_fichier_illisible_verrouille(self, sans_redis):
        """Dans le doute, ne pas ouvrir : une erreur de lecture ne doit pas
        offrir la creation d'un compte administrateur."""
        import admin_module
        sans_redis.write_text("ceci: n'est pas: du YAML: valide:", encoding="utf-8")
        assert _executer(admin_module._setup_completed()) is True

    def test_le_drapeau_redis_suffit(self, tmp_path, monkeypatch):
        """L'ancien mecanisme reste valable quand Redis repond."""
        import admin_module

        class _RedisPlein:
            @staticmethod
            async def get(_cle):
                return "1"

        monkeypatch.setattr(admin_module, "_r", lambda: _RedisPlein())
        monkeypatch.setattr(admin_module, "AUTHELIA_YML",
                            tmp_path / "absent.yml")
        assert _executer(admin_module._setup_completed()) is True


# ============================================================================
# Ecart entre ce qui est ecrit et ce qu'Orthanc applique
# ============================================================================

class TestConfigurationEffective:
    """Ecrire une valeur ne prouve pas qu'Orthanc l'applique.

    Trois facons de diverger sans que rien ne le signale : une variable
    ORTHANC__* du compose qui ecrase le fichier, un champ declare au mauvais
    endroit de l'arborescence, un redemarrage jamais fait.

    Le deuxieme cas n'est pas theorique : StudyListColumns vivait sous
    OrthancExplorer2 alors qu'Explorer le lit sous UiOptions. Le reglage
    n'avait jamais eu d'effet depuis qu'il existe, et c'est cette
    verification qui l'a trouve.
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
    def _repondre(monkeypatch, systeme=None, ui=None):
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

    def test_aucun_ecart(self, config, monkeypatch):
        import admin_module
        self._repondre(
            monkeypatch,
            systeme={"Name": "PACS Cuffel", "DicomAet": "PACSCUFFEL"},
            ui={"UiOptions": {"StudyListColumns": ["PatientID"]}},
        )
        assert _executer(admin_module._check_effective_config()) == []

    def test_ecart_detecte(self, config, monkeypatch):
        """Le cas d'une variable d'environnement qui ecrase le fichier."""
        import admin_module
        self._repondre(
            monkeypatch,
            systeme={"Name": "Autre nom", "DicomAet": "PACSCUFFEL"},
            ui={"UiOptions": {"StudyListColumns": ["PatientID"]}},
        )
        ecarts = _executer(admin_module._check_effective_config())
        assert len(ecarts) == 1
        assert ecarts[0]["champ"] == "Name"
        assert ecarts[0]["dans_le_fichier"] == "PACS Cuffel"
        assert ecarts[0]["applique_par_orthanc"] == "Autre nom"

    def test_champ_mal_place_detecte(self, config, monkeypatch):
        """Le defaut reel : Orthanc applique ses colonnes par defaut parce
        que le champ est ailleurs dans l'arborescence."""
        import admin_module
        self._repondre(
            monkeypatch,
            systeme={"Name": "PACS Cuffel", "DicomAet": "PACSCUFFEL"},
            ui={"UiOptions": {"StudyListColumns": ["PatientBirthDate", "modalities"]}},
        )
        ecarts = _executer(admin_module._check_effective_config())
        champs = [e["champ"] for e in ecarts]
        assert "OrthancExplorer2.UiOptions.StudyListColumns" in champs

    def test_champ_absent_du_fichier_ignore(self, tmp_path, monkeypatch):
        """Non declare = valeur par defaut d'Orthanc : ce n'est pas un ecart."""
        import admin_module
        fichier = tmp_path / "orthanc.json"
        fichier.write_text('{"Name": "PACS"}', encoding="utf-8")
        monkeypatch.setattr(admin_module, "ORTHANC_JSON", fichier)
        self._repondre(monkeypatch, systeme={"Name": "PACS", "DicomPort": 4242},
                       ui={})
        assert _executer(admin_module._check_effective_config()) == []

    def test_orthanc_muet(self, config, monkeypatch):
        """Rien a comparer ne doit pas se traduire par une alerte."""
        import admin_module

        async def _casse(*a, **k):
            raise ConnectionError("Orthanc ne repond pas")

        monkeypatch.setattr(admin_module, "_orthanc", _casse)
        assert _executer(admin_module._check_effective_config()) == []

    def test_droits_calcules_hors_verification(self):
        """EnableShares vaut vrai pour un administrateur et faux pour un
        utilisateur externe : c'est un droit, pas un reglage. Le comparer au
        fichier produirait une alerte permanente."""
        from admin_module import ORTHANC_VERIFIABLE
        for champ in ("OrthancExplorer2.UiOptions.EnableShares",
                      "OrthancExplorer2.UiOptions.EnableViewerQuickButton"):
            assert champ not in ORTHANC_VERIFIABLE

    def test_colonnes_declarees_sous_uioptions(self):
        """Garde-fou d'emplacement : Explorer lit ce champ sous UiOptions.
        Le declarer ailleurs redonnerait un reglage sans effet, silencieux."""
        from admin_module import ORTHANC_EDITABLE_PATHS
        assert "OrthancExplorer2.UiOptions.StudyListColumns" in ORTHANC_EDITABLE_PATHS
        assert "OrthancExplorer2.StudyListColumns" not in ORTHANC_EDITABLE_PATHS
