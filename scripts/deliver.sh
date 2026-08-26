#!/bin/bash
# Copie des fichiers suivis vers une installation, en refusant tout ce qui
# lui appartient en propre.
#
# Raison d'etre : une livraison faite fichier par fichier a la main a ecrase
# services/authelia/config/configuration.yml sur une installation en service.
# Ce fichier est gitignore parce qu'il porte le domaine, les URL de
# redirection et les regles d'acces de CETTE installation. Le remplacer par la
# copie d'un poste de developpement a mis le domaine a pacs.localhost : plus
# aucune regle ne correspondait a l'URL reelle, /api/verify repondait 401, et
# la page de connexion est devenue inatteignable.
#
# Le garde-fou est donc : tout chemin ignore par git est refuse, sans
# exception et sans option pour passer outre. Un fichier ignore est par
# definition propre a l'installation ; il n'y a aucun cas ou l'ecraser depuis
# un autre poste soit la bonne chose a faire.
#
# Usage : scripts/deliver.sh <destination> <fichier>...
set -euo pipefail

if [ $# -lt 2 ]; then
    echo "usage: $0 <destination> <fichier>..." >&2
    exit 2
fi

destination=$1
shift

if [ ! -d "$destination" ]; then
    echo "destination introuvable : $destination" >&2
    exit 1
fi

refuses=()
absents=()
non_suivis=()
a_copier=()

for f in "$@"; do
    if [ ! -f "$f" ]; then
        absents+=("$f")
        continue
    fi
    if git check-ignore -q "$f"; then
        refuses+=("$f")
        continue
    fi
    if ! git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
        non_suivis+=("$f")
        continue
    fi
    a_copier+=("$f")
done

if [ ${#refuses[@]} -gt 0 ]; then
    echo "REFUSE — ces fichiers appartiennent a l'installation, pas au depot :" >&2
    printf '  %s\n' "${refuses[@]}" >&2
    echo "Rien n'a ete copie." >&2
    exit 1
fi

if [ ${#absents[@]} -gt 0 ]; then
    echo "introuvables :" >&2
    printf '  %s\n' "${absents[@]}" >&2
    exit 1
fi

if [ ${#non_suivis[@]} -gt 0 ]; then
    echo "non suivis par git — a ajouter d'abord :" >&2
    printf '  %s\n' "${non_suivis[@]}" >&2
    exit 1
fi

for f in "${a_copier[@]}"; do
    mkdir -p "$destination/$(dirname "$f")"
    cp -- "$f" "$destination/$f"
    printf '  livre  %s\n' "$f"
done

echo "${#a_copier[@]} fichier(s) livre(s) vers $destination"
