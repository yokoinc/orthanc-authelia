"""Translation catalogues.

One language = one translations/<code>.json file. No list of languages is
written in the code: the service discovers the files present. Adding a language
means dropping in a file -- see "Adding a language" in the README.

Each file holds sections ("admin", "api", "setup", "ui", "js", "oe2") of flat
keys, plus a "meta" section giving the language's name as the selector shows it
("Français", "Deutsch").

A key missing from a file falls back to English, then to the key itself: an
incomplete translation stays usable, it simply shows English where it has no
text yet. The CI, for its part, requires complete files.

Variables are written {name}, with the same names in every language (checked by
tests/test_i18n.py).
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("auth-service.i18n")

# /app/translations in the image; overridable to mount one's own files without
# rebuilding (see the README).
TRANSLATIONS_DIR = Path(
    os.getenv("I18N_DIR", str(Path(__file__).resolve().parent / "translations"))
)
LANGUE_REPLI = "en"
_CODE_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")

# (file name, mtime) -> contents. Re-read only when the file changes: a
# catalogue is consulted on every request.
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _charger(code: str) -> dict[str, Any]:
    chemin = TRANSLATIONS_DIR / f"{code}.json"
    try:
        mtime = chemin.stat().st_mtime
    except OSError:
        return {}
    en_cache = _cache.get(code)
    if en_cache and en_cache[0] == mtime:
        return en_cache[1]
    try:
        data = json.loads(chemin.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("the file must contain a JSON object")
    except (OSError, ValueError) as e:
        # A broken file must not bring the service down: the language drops out
        # of the selector, and the log says why.
        logger.warning("translations/%s.json unreadable, ignored: %s", code, e)
        data = {}
    _cache[code] = (mtime, data)
    return data


def langues_disponibles() -> dict[str, str]:
    """{code: display name} of every valid language file, sorted by code."""
    langues = {}
    try:
        fichiers = sorted(TRANSLATIONS_DIR.glob("*.json"))
    except OSError:
        fichiers = []
    for f in fichiers:
        code = f.stem.lower()
        if not _CODE_RE.match(code):
            continue
        data = _charger(code)
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else None
        if meta:
            langues[code] = str(meta.get("name") or code)
    return langues


def normaliser(valeur: str | None) -> str:
    """"fr_FR.UTF-8", "FR", "fr-FR" -> "fr". Empty string when there is nothing."""
    if not valeur:
        return ""
    return re.split(r"[_.:@-]", str(valeur).strip().lower(), maxsplit=1)[0]


def depuis_accept_language(entete: str | None, disponibles) -> str:
    """First language of an Accept-Language header that has a file, "" otherwise.

    "fr-FR,fr;q=0.9,en;q=0.8" -> "fr". Entries are taken by decreasing q, then
    in the order written; q=0 means "not this one". A browser asking only for
    languages without a file gets "": the caller falls back on the
    installation's default.
    """
    choix = []
    for rang, morceau in enumerate((entete or "").split(",")):
        parties = morceau.strip().split(";")
        code = normaliser(parties[0])
        if not code or code == "*":
            continue
        q = 1.0
        for p in parties[1:]:
            p = p.strip()
            if p.startswith("q="):
                try:
                    q = float(p[2:])
                except ValueError:
                    q = 0.0
        if q > 0:
            choix.append((-q, rang, code))
    for _, _, code in sorted(choix):
        if code in disponibles:
            return code
    return ""


# Language of the request being served, set by admin_module.langue_gate. A
# context variable and not a global: requests run concurrently, each keeps its own.
_langue_requete: contextvars.ContextVar[str] = contextvars.ContextVar("langue_requete", default="")


def definir_langue_requete(code: str) -> contextvars.Token:
    return _langue_requete.set(code)


def oublier_langue_requete(jeton: contextvars.Token) -> None:
    _langue_requete.reset(jeton)


def langue_requete() -> str:
    return _langue_requete.get()


def section(nom: str, langue: str) -> dict[str, str]:
    """A whole section, completed with English for the missing keys."""
    resultat = dict(_charger(LANGUE_REPLI).get(nom) or {})
    if langue != LANGUE_REPLI:
        resultat.update(_charger(langue).get(nom) or {})
    return resultat


def texte(nom_section: str, cle: str, langue: str, **variables: Any) -> str:
    """The text of a key, variables substituted. Never raises."""
    modele = (_charger(langue).get(nom_section) or {}).get(cle)
    if modele is None:
        modele = (_charger(LANGUE_REPLI).get(nom_section) or {}).get(cle)
    if modele is None:
        logger.warning("missing translation key %s.%s", nom_section, cle)
        return cle
    if not variables:
        return modele
    try:
        return modele.format(**variables)
    except (KeyError, IndexError, ValueError) as e:
        # Unclosed brace or a variable renamed in a translation: show the raw
        # text rather than turn an error message into a 500.
        logger.warning("translation %s.%s (%s) not formattable: %s", nom_section, cle, langue, e)
        return modele
