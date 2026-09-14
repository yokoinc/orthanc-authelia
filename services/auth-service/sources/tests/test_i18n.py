"""Translation catalogues: completeness, consistency, and adding a language.

What is guaranteed here, and what nothing else would check before a user hits
a raw key on screen:

  - every key called by the code exists in the reference catalogue (en);
  - every language has the same keys, with the same {variables};
  - a language dropped in as a plain file is discovered, and its missing keys
    fall back to English;
  - the panel renders with no leftover [[...]] token, in every language.
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


@pytest.mark.parametrize("entete, attendu", [
    ("fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7", "fr"),
    ("en-GB", "en"),
    ("de-DE,de;q=0.9", ""),
    ("de;q=1, en;q=0.4, fr;q=0.5", "fr"),
    ("fr;q=0, en", "en"),
    ("*", ""),
    ("", ""),
    (None, ""),
    ("fr;q=abc, en", "en"),
])
def test_accept_language(entete, attendu):
    assert i18n.depuis_accept_language(entete, {"fr": "Français", "en": "English"}) == attendu


def _langues() -> list[str]:
    return sorted(p.stem for p in TRADUCTIONS.glob("*.json"))


def _lire(relatif: str) -> str:
    return (SOURCES / relatif).read_text(encoding="utf-8")


# ----------------------------------------------------------------------------
# Keys called by the code
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
        assert bloc, f"{tuple_nom} not found in admin_module.py"
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
    assert cles, f"no {section} key found: the extraction no longer works"
    absentes = sorted(cles - set(_catalogue(REFERENCE).get(section, {})))
    assert not absentes, f"{section} keys called but missing from {REFERENCE}.json: {absentes}"


# ----------------------------------------------------------------------------
# Consistency between languages
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("code", [c for c in _langues() if c != REFERENCE])
def test_languages_have_the_same_keys_and_variables(code):
    ref, autre = _catalogue(REFERENCE), _catalogue(code)
    assert set(ref) == set(autre), f"different sections: {set(ref) ^ set(autre)}"
    for section, cles in ref.items():
        assert set(cles) == set(autre[section]), \
            f"{code}.json, section {section}: {sorted(set(cles) ^ set(autre[section]))}"
        for cle, texte in cles.items():
            assert set(_VARIABLE_RE.findall(texte)) == set(_VARIABLE_RE.findall(autre[section][cle])), \
                f"{code}.json {section}.{cle}: variables differ from {REFERENCE}.json"


@pytest.mark.parametrize("code", _langues())
def test_every_language_names_itself(code):
    assert _catalogue(code)["meta"]["name"].strip()


# ----------------------------------------------------------------------------
# Adding a language = dropping in a file
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
    # Missing from the German file: English, not the raw key.
    assert i18n.texte("admin", "cancel", "de") == _catalogue("en")["admin"]["cancel"]
    assert i18n.section("admin", "de")["cancel"] == _catalogue("en")["admin"]["cancel"]


def test_a_broken_file_is_ignored_not_fatal(dossier_langues):
    (dossier_langues / "xx.json").write_text("{ not json", encoding="utf-8")
    assert "xx" not in i18n.langues_disponibles()
    assert "en" in i18n.langues_disponibles()


def test_a_mistranslated_variable_does_not_raise(dossier_langues):
    (dossier_langues / "de.json").write_text(json.dumps({
        "meta": {"name": "Deutsch"},
        "admin": {"account_deleted": "Konto {falscher_name gelöscht"},
    }), encoding="utf-8")
    assert i18n.texte("admin", "account_deleted", "de", name="x") == "Konto {falscher_name gelöscht"


@pytest.mark.parametrize("brut, attendu", [
    ("fr_FR.UTF-8", "fr"), ("FR", "fr"), ("en-US", "en"), ("", ""), (None, ""),
])
def test_language_codes_are_normalised(brut, attendu):
    assert i18n.normaliser(brut) == attendu
