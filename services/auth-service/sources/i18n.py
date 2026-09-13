"""Catalogues de traduction.

Une langue = un fichier translations/<code>.json. Aucune liste de langues n'est
ecrite dans le code : le service decouvre les fichiers presents. Ajouter une
langue revient a deposer un fichier -- voir « Adding a language » dans le
README.

Chaque fichier porte des sections (« admin », « api », « setup », « ui »,
« js », « oe2 ») de cles plates, plus une section « meta » qui donne le nom de
la langue tel qu'on l'affiche dans le selecteur (« Français », « Deutsch »).

Une cle absente d'un fichier retombe sur l'anglais, puis sur la cle elle-meme :
une traduction incomplete reste utilisable, elle montre simplement de
l'anglais la ou elle n'a pas encore de texte. La CI, elle, exige des fichiers
complets.

Les variables s'ecrivent {nom}, avec les memes noms dans toutes les langues
(controle par tests/test_i18n.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("auth-service.i18n")

# /app/translations dans l'image ; surchargeable pour monter ses propres
# fichiers sans reconstruire (voir le README).
TRANSLATIONS_DIR = Path(
    os.getenv("I18N_DIR", str(Path(__file__).resolve().parent / "translations"))
)
LANGUE_REPLI = "en"
_CODE_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")

# (nom du fichier, mtime) -> contenu. Relu seulement quand le fichier change :
# un catalogue est consulte a chaque requete.
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
            raise ValueError("le fichier doit contenir un objet JSON")
    except (OSError, ValueError) as e:
        # Un fichier casse ne doit pas faire tomber le service : la langue
        # disparait du selecteur, et le journal dit pourquoi.
        logger.warning("translations/%s.json unreadable, ignored: %s", code, e)
        data = {}
    _cache[code] = (mtime, data)
    return data


def langues_disponibles() -> dict[str, str]:
    """{code: nom affiche} de chaque fichier de langue valide, trie par code."""
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
    """« fr_FR.UTF-8 », « FR », « fr-FR » -> « fr ». Chaine vide si rien."""
    if not valeur:
        return ""
    return re.split(r"[_.:@-]", str(valeur).strip().lower(), maxsplit=1)[0]


def section(nom: str, langue: str) -> dict[str, str]:
    """Une section complete, completee par l'anglais pour les cles manquantes."""
    resultat = dict(_charger(LANGUE_REPLI).get(nom) or {})
    if langue != LANGUE_REPLI:
        resultat.update(_charger(langue).get(nom) or {})
    return resultat


def texte(nom_section: str, cle: str, langue: str, **variables: Any) -> str:
    """Le texte d'une cle, variables substituees. Jamais d'exception."""
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
        # Accolade mal fermee ou variable renommee dans une traduction : on
        # montre le texte brut plutot que de transformer un message d'erreur en
        # erreur 500.
        logger.warning("translation %s.%s (%s) not formattable: %s", nom_section, cle, langue, e)
        return modele
