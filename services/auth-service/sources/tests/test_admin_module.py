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
        from admin_module import _ecrire_changements
        source = """{
  // Nom affiche dans l'interface
  "Name": "Orthanc",

  // Titre applicatif DICOM, 16 caracteres au plus
  "DicomAet": "ORTHANC"
}"""
        out = _ecrire_changements(source, {"Name": "PACS"})
        assert out.count("//") == 2
        assert "Nom affiche dans l'interface" in out
        assert self._relire(out) == {"Name": "PACS", "DicomAet": "ORTHANC"}

    def test_seule_la_ligne_visee_change(self):
        """Une modification ne doit pas reformater le reste du fichier."""
        from admin_module import _ecrire_changements
        source = '{\n  "A": 1,\n  "B": 2,\n  "C": 3\n}'
        out = _ecrire_changements(source, {"B": 20})
        avant, apres = source.splitlines(), out.splitlines()
        assert len(avant) == len(apres)
        assert [i for i, (a, b) in enumerate(zip(avant, apres)) if a != b] == [2]

    def test_cle_citee_dans_un_commentaire(self):
        """Le piege classique : le nom de la cle apparait aussi en commentaire."""
        from admin_module import _ecrire_changements
        source = """{
  // Ne pas confondre avec "Name" du bloc DicomWeb ci-dessous
  "Name": "Orthanc"
}"""
        out = _ecrire_changements(source, {"Name": "PACS"})
        assert 'avec "Name" du bloc' in out       # le commentaire est intact
        assert self._relire(out) == {"Name": "PACS"}

    def test_accolade_dans_une_chaine(self):
        """Une accolade entre guillemets ne doit pas etre lue comme un bloc."""
        from admin_module import _ecrire_changements
        source = '{\n  "Motif": "prefixe{suffixe}",\n  "Name": "Orthanc"\n}'
        out = _ecrire_changements(source, {"Name": "PACS"})
        assert self._relire(out) == {"Motif": "prefixe{suffixe}", "Name": "PACS"}

    def test_commentaire_en_fin_de_ligne(self):
        """La valeur s'arrete avant le //, qui doit survivre tel quel."""
        from admin_module import _ecrire_changements
        source = '{\n  "Taille": 500, // en megaoctets\n  "Name": "Orthanc"\n}'
        out = _ecrire_changements(source, {"Taille": 800})
        assert "// en megaoctets" in out
        assert self._relire(out)["Taille"] == 800

    def test_cle_imbriquee(self):
        from admin_module import _ecrire_changements
        source = """{
  "Name": "Orthanc",
  "DicomWeb": {
    // Taille maximale d'un envoi STOW-RS
    "StowMaxSize": 500,
    "Enable": true
  }
}"""
        out = _ecrire_changements(source, {"DicomWeb.StowMaxSize": 1000})
        assert "Taille maximale" in out
        assert self._relire(out)["DicomWeb"] == {"StowMaxSize": 1000, "Enable": True}

    def test_cle_absente_ajoutee(self):
        """Orthanc laisse beaucoup de reglages implicites : les definir est un
        cas courant, pas une exception."""
        from admin_module import _ecrire_changements
        source = '{\n  // Reglages de base\n  "Name": "Orthanc"\n}'
        out = _ecrire_changements(source, {"DicomAlwaysAllowStore": False})
        assert "// Reglages de base" in out
        assert self._relire(out) == {"Name": "Orthanc", "DicomAlwaysAllowStore": False}
        # Meme indentation que ses voisines : une cle decalee se remarque, et
        # donne l'impression d'un fichier edite a la main a la va-vite.
        ligne = [l for l in out.splitlines() if "DicomAlwaysAllowStore" in l][0]
        assert ligne.startswith('  "'), repr(ligne)

    def test_cle_ajoutee_apres_un_commentaire_final(self):
        """Le commentaire de fin de bloc doit rester en dernier."""
        from admin_module import _ecrire_changements
        source = '{\n  "Name": "Orthanc"\n  // fin du bloc\n}'
        out = _ecrire_changements(source, {"DicomAet": "PACS"})
        assert self._relire(out) == {"Name": "Orthanc", "DicomAet": "PACS"}
        assert out.index('"DicomAet"') < out.index("// fin du bloc")

    def test_ajout_dans_un_objet_imbrique(self):
        from admin_module import _ecrire_changements
        source = '{\n  "DicomWeb": {\n    "Enable": true\n  }\n}'
        out = _ecrire_changements(source, {"DicomWeb.StowMaxSize": 500})
        assert self._relire(out)["DicomWeb"] == {"Enable": True, "StowMaxSize": 500}
        ligne = [l for l in out.splitlines() if "StowMaxSize" in l][0]
        assert ligne.startswith('    "'), repr(ligne)

    def test_parent_absent_refuse(self):
        """Creer une arborescence demanderait de deviner une mise en forme :
        on prefere le signaler et laisser l'appelant regenerer."""
        from admin_module import _ecrire_changements
        with pytest.raises(ValueError, match="parent absent"):
            _ecrire_changements('{\n  "Name": "Orthanc"\n}',
                                {"Absent.Cle": 1})

    def test_plusieurs_changements_a_la_fois(self):
        from admin_module import _ecrire_changements
        source = """{
  // en-tete
  "Name": "Orthanc",
  "DicomAet": "ORTHANC",
  "DicomPort": 4242
}"""
        out = _ecrire_changements(source, {
            "Name": "PACS", "DicomPort": 11112, "DicomCheckCalledAet": True,
        })
        assert "// en-tete" in out
        assert self._relire(out) == {
            "Name": "PACS", "DicomAet": "ORTHANC", "DicomPort": 11112,
            "DicomCheckCalledAet": True,
        }

    def test_types_scalaires(self):
        """booleen, entier, chaine et null doivent se relire a l'identique."""
        from admin_module import _ecrire_changements
        source = '{\n  "A": 1,\n  "B": "x",\n  "C": true,\n  "D": null\n}'
        out = _ecrire_changements(source, {"A": 42, "B": "y", "C": False, "D": "z"})
        assert self._relire(out) == {"A": 42, "B": "y", "C": False, "D": "z"}

    def test_fichier_reel_du_depot(self):
        """Le fichier livre : aucun commentaire ne doit disparaitre."""
        from admin_module import _ecrire_changements
        from pathlib import Path as _P

        exemple = _P(__file__).resolve().parents[4] / "orthanc.json.example"
        if not exemple.exists():           # arborescence reduite (image de test)
            pytest.skip("orthanc.json.example hors de l'arborescence")

        source = exemple.read_text(encoding="utf-8")
        avant = source.count("//")
        out = _ecrire_changements(source, {"Name": "PACS Cuffel"})
        assert out.count("//") == avant
        assert self._relire(out)["Name"] == "PACS Cuffel"


# ============================================================================
# Viewer par defaut des liens de partage
# ============================================================================

class TestViewerParDefaut:
    """Le viewer preselectionne quand on partage un examen.

    La valeur est lue dans le .env a chaque appel, et non au demarrage : c'est
    ce qui permet au changement de prendre effet sans recreer le container,
    Explorer redemandant ces reglages a chaque ouverture du menu de partage.
    Ces tests verrouillent ce comportement et le repli sur une valeur sure.
    """

    @pytest.fixture
    def env_temporaire(self, tmp_path, monkeypatch):
        """Un .env isole, et un fichier de reglages absent.

        Le fichier de reglages est explicitement pointe vers un chemin
        inexistant : ces cas-ci verifient la reprise de l'ancienne variable
        d'environnement, il ne faut pas qu'un fichier de reglages tramant
        dans l'image de test la court-circuite.
        """
        import admin_module

        fichier = tmp_path / ".env"
        fichier.write_text("PUBLIC_URL=https://exemple.fr\n", encoding="utf-8")
        monkeypatch.setattr(admin_module, "ENV_FILE", fichier)
        monkeypatch.setattr(admin_module, "SETTINGS_FILE",
                            tmp_path / "absent" / "settings.json")
        return fichier

    def test_absent_du_env_repli_sur_ohif(self, env_temporaire):
        """Une installation existante n'a pas la variable : elle doit marcher."""
        import auth_service
        assert auth_service._default_share_viewer() == "ohif-viewer-publication"

    def test_valeur_du_env_prise_en_compte(self, env_temporaire):
        import auth_service
        env_temporaire.write_text(
            "SHARE_DEFAULT_VIEWER=stone-viewer-publication\n", encoding="utf-8")
        assert auth_service._default_share_viewer() == "stone-viewer-publication"

    def test_valeur_inconnue_ignoree(self, env_temporaire):
        """Une faute de frappe ne doit pas casser le menu de partage."""
        import auth_service
        env_temporaire.write_text("SHARE_DEFAULT_VIEWER=nimporte-quoi\n",
                                  encoding="utf-8")
        assert auth_service._default_share_viewer() == "ohif-viewer-publication"

    def test_lien_instantane_refuse(self, env_temporaire):
        """viewer-instant-link n'est pas une publication : Explorer construit
        l'URL lui-meme, il n'y a pas de page de partage a servir."""
        import auth_service
        env_temporaire.write_text("SHARE_DEFAULT_VIEWER=viewer-instant-link\n",
                                  encoding="utf-8")
        assert auth_service._default_share_viewer() == "ohif-viewer-publication"

    def test_relu_a_chaque_appel(self, env_temporaire):
        """Le point qui compte : pas de valeur figee au chargement du module."""
        import auth_service

        env_temporaire.write_text("SHARE_DEFAULT_VIEWER=volview-viewer-publication\n",
                                  encoding="utf-8")
        premier = auth_service._default_share_viewer()

        env_temporaire.write_text("SHARE_DEFAULT_VIEWER=stone-viewer-publication\n",
                                  encoding="utf-8")
        second = auth_service._default_share_viewer()

        assert premier == "volview-viewer-publication"
        assert second == "stone-viewer-publication"

    def test_env_illisible_repli(self, monkeypatch):
        """Sans .env accessible, le menu de partage doit rester utilisable."""
        import admin_module
        import auth_service

        monkeypatch.setattr(admin_module, "ENV_FILE",
                            Path("/chemin/qui/n/existe/pas/.env"))
        assert auth_service._default_share_viewer() == "ohif-viewer-publication"


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
        from admin_module import _ecrire_reglage, _lire_reglage
        _ecrire_reglage("share_default_viewer", "stone-viewer-publication")
        assert _lire_reglage("share_default_viewer") == "stone-viewer-publication"

    def test_dossier_cree_au_besoin(self, reglages):
        """Une installation neuve n'a pas encore le fichier."""
        from admin_module import _ecrire_reglage
        assert not reglages.parent.exists()
        _ecrire_reglage("langue", "fr")
        assert reglages.exists()

    def test_plusieurs_reglages_coexistent(self, reglages):
        from admin_module import _ecrire_reglage, _lire_reglage
        _ecrire_reglage("a", 1)
        _ecrire_reglage("b", "deux")
        assert (_lire_reglage("a"), _lire_reglage("b")) == (1, "deux")

    def test_reprise_de_l_ancienne_variable(self, reglages, tmp_path):
        """Une installation existante a le reglage dans son .env : il doit
        continuer a s'appliquer tant qu'on ne l'a pas redefini."""
        from admin_module import _lire_reglage
        (tmp_path / ".env").write_text(
            "SHARE_DEFAULT_VIEWER=stone-viewer-publication\n", encoding="utf-8")
        assert _lire_reglage("share_default_viewer",
                             "SHARE_DEFAULT_VIEWER") == "stone-viewer-publication"

    def test_le_fichier_prime_sur_le_env(self, reglages, tmp_path):
        """Apres la premiere ecriture, la ligne du .env devient inerte."""
        from admin_module import _ecrire_reglage, _lire_reglage
        (tmp_path / ".env").write_text(
            "SHARE_DEFAULT_VIEWER=stone-viewer-publication\n", encoding="utf-8")
        _ecrire_reglage("share_default_viewer", "volview-viewer-publication")
        assert _lire_reglage("share_default_viewer",
                             "SHARE_DEFAULT_VIEWER") == "volview-viewer-publication"

    def test_fichier_illisible_ne_casse_rien(self, reglages):
        """Un JSON corrompu doit degrader vers les valeurs par defaut, pas
        empecher le service de repondre."""
        from admin_module import _lire_reglage
        reglages.parent.mkdir(parents=True)
        reglages.write_text("{ceci n'est pas du JSON", encoding="utf-8")
        assert _lire_reglage("share_default_viewer", defaut="ohif") == "ohif"

    def test_ecriture_atomique(self, reglages):
        """Aucun fichier temporaire ne doit subsister apres l'ecriture."""
        from admin_module import _ecrire_reglage
        _ecrire_reglage("a", 1)
        restes = [f.name for f in reglages.parent.iterdir()
                  if f.name != "settings.json"]
        assert restes == [], restes

    def test_aucun_secret_dans_le_fichier(self, reglages):
        """Garde-fou de conception : ce fichier n'est pas un coffre. Il vit
        dans data/, echappe au .gitignore des secrets, et pourrait etre
        recopie sans precaution."""
        from admin_module import _ecrire_reglage
        _ecrire_reglage("share_default_viewer", "ohif-viewer-publication")
        contenu = reglages.read_text(encoding="utf-8").lower()
        for interdit in ("password", "secret", "token", "_key"):
            assert interdit not in contenu, interdit
