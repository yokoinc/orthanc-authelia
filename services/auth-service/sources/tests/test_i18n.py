"""Catalogues de traduction : completude, coherence, et ajout d'une langue.

Ce qui est garanti ici, et que rien d'autre ne verifierait avant qu'un
utilisateur tombe sur une cle brute a l'ecran :

  - toute cle appelee par le code existe dans le catalogue de reference (en) ;
  - toutes les langues ont les memes cles, avec les memes {variables} ;
  - une langue deposee comme simple fichier est decouverte, et ses cles
    manquantes retombent sur l'anglais ;
  - le panneau se rend sans jeton [[...]] residuel, dans chaque langue.
"""
import json
import re
from pathlib import Path

import pytest

import admin_module
import i18n

SOURCES = Path(__file__).resolve().parent.parent
TRADUCTIONS = SOURCES / "translations"
REFERENCE = "en"
_VARIABLE_RE = re.compile(r"\{(\w+)\}")


def _catalogue(code: str) -> dict:
    return json.loads((TRADUCTIONS / f"{code}.json").read_text(encoding="utf-8"))


def _langues() -> list[str]:
    return sorted(p.stem for p in TRADUCTIONS.glob("*.json"))


def _lire(relatif: str) -> str:
    return (SOURCES / relatif).read_text(encoding="utf-8")


# ----------------------------------------------------------------------------
# Cles appelees par le code
# ----------------------------------------------------------------------------

def _cles_admin_utilisees() -> set[str]:
    html, js = _lire("templates/admin.html"), _lire("static/admin.js")
    cles = set(re.findall(r"\[\[([A-Za-z0-9_.]+)\]\]", html))
    cles |= set(re.findall(r"\bt\('([A-Za-z0-9_.]+)'", js))
    # t(condition ? 'a' : 'b', ...)
    for a, b in re.findall(r"\bt\([^?)]+\?\s*'([A-Za-z0-9_.]+)'\s*:\s*'([A-Za-z0-9_.]+)'", js):
        cles |= {a, b}
    return cles


def _cles_api_utilisees() -> set[str]:
    cles = set()
    for fichier in ("admin_module.py", "auth_service.py"):
        src = _lire(fichier)
        cles |= set(re.findall(r'_msg\(\s*"([A-Za-z0-9_.]+)"', src))
        # _msg("a" if cond else "b", ...)
        for a, b in re.findall(r'_msg\(\s*"([A-Za-z0-9_.]+)"\s+if\s+[^"]+\s+else\s+"([A-Za-z0-9_.]+)"', src):
            cles |= {a, b}
    src = _lire("admin_module.py")
    for tuple_nom, prefixe in (("ORTHANC_AIDE", "orthanc_help"), ("SESSION_KEYS", "session_label")):
        bloc = re.search(rf"^{tuple_nom} = \((.*?)^\)", src, re.S | re.M)
        assert bloc, f"{tuple_nom} introuvable dans admin_module.py"
        cles |= {f"{prefixe}.{n}" for n in re.findall(r'"([A-Za-z.]+)"', bloc.group(1))}
    for nom in ("accounts", "orthanc", "authelia"):
        cles.add(f"backup_label.{nom}")
    return cles


def _cles_setup_utilisees() -> set[str]:
    html = _lire("templates/setup.html")
    return set(re.findall(r"""\bt\(['"]([A-Za-z0-9_.]+)['"]\)""", html)) \
        | set(re.findall(r'data-t="([A-Za-z0-9_.]+)"', html))


def _cles_ui_utilisees() -> set[str]:
    src = _lire("auth_service.py")
    return set(re.findall(r'(?:ui_translations|TRANSLATIONS\["ui"\])\["([A-Za-z0-9_.]+)"\]', src))


def _cles_oe2_utilisees() -> set[str]:
    js = _lire("static/oe2-menu.js")
    return set(re.findall(r'"(?:shares|admin|logout)-?[a-z]*",\s*"([a-z]+)"\]', js)) \
        | set(re.findall(r'libelle\("([a-z]+)"\)', js)) \
        | set(re.findall(r'makeItem\("[a-z-]+",\s*"[a-z-]+",\s*"([a-z]+)"', js))


@pytest.mark.parametrize("section, utilisees", [
    ("admin", _cles_admin_utilisees),
    ("api", _cles_api_utilisees),
    ("setup", _cles_setup_utilisees),
    ("ui", _cles_ui_utilisees),
    ("oe2", _cles_oe2_utilisees),
])
def test_every_key_used_by_the_code_exists(section, utilisees):
    cles = utilisees()
    assert cles, f"aucune cle {section} trouvee : l'extraction ne fonctionne plus"
    absentes = sorted(cles - set(_catalogue(REFERENCE).get(section, {})))
    assert not absentes, f"cles {section} appelees mais absentes de {REFERENCE}.json : {absentes}"


# ----------------------------------------------------------------------------
# Coherence entre langues
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("code", [c for c in _langues() if c != REFERENCE])
def test_languages_have_the_same_keys_and_variables(code):
    ref, autre = _catalogue(REFERENCE), _catalogue(code)
    assert set(ref) == set(autre), f"sections differentes : {set(ref) ^ set(autre)}"
    for section, cles in ref.items():
        assert set(cles) == set(autre[section]), \
            f"{code}.json, section {section} : {sorted(set(cles) ^ set(autre[section]))}"
        for cle, texte in cles.items():
            assert set(_VARIABLE_RE.findall(texte)) == set(_VARIABLE_RE.findall(autre[section][cle])), \
                f"{code}.json {section}.{cle} : variables differentes de {REFERENCE}.json"


@pytest.mark.parametrize("code", _langues())
def test_every_language_names_itself(code):
    assert _catalogue(code)["meta"]["name"].strip()


# ----------------------------------------------------------------------------
# Ajouter une langue = deposer un fichier
# ----------------------------------------------------------------------------

@pytest.fixture
def dossier_langues(tmp_path, monkeypatch):
    for f in TRADUCTIONS.glob("*.json"):
        (tmp_path / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(i18n, "TRANSLATIONS_DIR", tmp_path)
    i18n._cache.clear()
    yield tmp_path
    i18n._cache.clear()


def test_a_dropped_file_adds_a_language_with_english_fallback(dossier_langues):
    (dossier_langues / "de.json").write_text(json.dumps({
        "meta": {"name": "Deutsch"},
        "admin": {"save": "Speichern"},
    }), encoding="utf-8")

    assert i18n.langues_disponibles()["de"] == "Deutsch"
    assert i18n.texte("admin", "save", "de") == "Speichern"
    # Absent du fichier allemand : l'anglais, pas la cle brute.
    assert i18n.texte("admin", "cancel", "de") == _catalogue("en")["admin"]["cancel"]
    assert i18n.section("admin", "de")["cancel"] == _catalogue("en")["admin"]["cancel"]


def test_a_broken_file_is_ignored_not_fatal(dossier_langues):
    (dossier_langues / "xx.json").write_text("{ pas du json", encoding="utf-8")
    assert "xx" not in i18n.langues_disponibles()
    assert "en" in i18n.langues_disponibles()


def test_a_mistranslated_variable_does_not_raise(dossier_langues):
    (dossier_langues / "de.json").write_text(json.dumps({
        "meta": {"name": "Deutsch"},
        "admin": {"account_deleted": "Konto {nom_errone gelöscht"},
    }), encoding="utf-8")
    assert i18n.texte("admin", "account_deleted", "de", name="x") == "Konto {nom_errone gelöscht"


@pytest.mark.parametrize("brut, attendu", [
    ("fr_FR.UTF-8", "fr"), ("FR", "fr"), ("en-US", "en"), ("", ""), (None, ""),
])
def test_language_codes_are_normalised(brut, attendu):
    assert i18n.normaliser(brut) == attendu
