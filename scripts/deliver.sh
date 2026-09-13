#!/bin/bash
# Copies tracked files to an installation, refusing anything that belongs to
# that installation alone.
#
# Rationale: a delivery done file by file, by hand, overwrote
# services/authelia/config/configuration.yml on a live installation. That file
# is gitignored because it carries the domain, the redirect URLs and the access
# rules of THIS installation. Replacing it with a copy from a development
# machine set the domain to pacs.localhost: no rule matched the real URL any
# more, /api/verify answered 401, and the login page became unreachable.
#
# The safeguard is therefore: any path ignored by git is refused, without
# exception and with no override option. An ignored file is by definition
# specific to the installation. There is no case where overwriting it from
# another machine is the right thing to do.
#
# Usage: scripts/deliver.sh <destination> <file>...
set -euo pipefail

if [ $# -lt 2 ]; then
    echo "usage: $0 <destination> <file>..." >&2
    exit 2
fi

destination=$1
shift

if [ ! -d "$destination" ]; then
    echo "destination not found: $destination" >&2
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
    echo "REFUSED — these files belong to the installation, not to the repository:" >&2
    printf '  %s\n' "${refuses[@]}" >&2
    echo "Nothing was copied." >&2
    exit 1
fi

if [ ${#absents[@]} -gt 0 ]; then
    echo "not found:" >&2
    printf '  %s\n' "${absents[@]}" >&2
    exit 1
fi

if [ ${#non_suivis[@]} -gt 0 ]; then
    echo "not tracked by git — add them first:" >&2
    printf '  %s\n' "${non_suivis[@]}" >&2
    exit 1
fi

for f in "${a_copier[@]}"; do
    mkdir -p "$destination/$(dirname "$f")"
    cp -- "$f" "$destination/$f"
    printf '  delivered  %s\n' "$f"
done

echo "${#a_copier[@]} file(s) delivered to $destination"
