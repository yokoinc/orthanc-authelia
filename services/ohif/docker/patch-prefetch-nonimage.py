#!/usr/bin/env python3
"""Stop OHIF's prefetcher from prefetching series that have no image.

THE PROBLEM
    A study carrying a structured report -- a CT Dose Record --
    showed a red "An error occurred" banner AT THE END of image loading,
    while the images displayed perfectly.

    Measured on a real installation on 2026-08-29: 70 SR series across 209
    studies, roughly one study in three. On the server side:

        GET /dicom-web/studies/.../instances/.../frames/1  ->  400
        Bad request: Cannot extract a frame from a DICOM file that does not
        have pixel data.

    Orthanc's answer is correct: the instance is an X-Ray Radiation Dose SR
    (1.2.840.10008.5.1.4.1.1.88.67), there are no pixels to extract.

THE CAUSE
    StudyPrefetcherService takes `displaySetService.getActiveDisplaySets()` and
    prefetches the next ones as soon as the active series has loaded -- hence a
    trigger AT THE END of loading, the signature that made it possible to
    isolate it.

    It filters NOTHING: neither sets marked `unsupported`, nor the modalities
    known to have no image. OHIF does maintain that list
    (thumbnailNoImageModalities: SR, SEG, RTSTRUCT, RTPLAN, RTDOSE, DOC,
    PMAP, RWV) and uses it elsewhere, in the study panel.

    Note: upstream does not enable this service by default. Our configuration
    turns it on, to speed up moving from one series to the next -- which
    matters on a PACS viewed through a tunnel.

THE FIX
    Filter the list at the source, with OHIF's own list. Prefetching keeps all
    its value on image series, and stops fetching pixels where there are none.

MAINTENANCE
    TO RECHECK ON EVERY OHIF UPGRADE. The script fails on purpose if the pattern
    has disappeared -- the build then stops, rather than producing an image in
    which the fix silently went missing. If upstream adds this filter, delete
    this file and its call in the Dockerfile.
"""
import io
import sys

CIBLE = "platform/core/src/services/StudyPrefetcherService/StudyPrefetcherService.ts"

IMPORT_AVANT = "import { DisplaySet } from '../../types';\n"
IMPORT_APRES = (
    "import { DisplaySet } from '../../types';\n"
    "// orthanc-authelia: list maintained by OHIF of modalities without images.\n"
    "import { thumbnailNoImageModalities } from '../../utils/thumbnailNoImageModalities';\n"
)

AVANT = "    const displaySets = [...displaySetService.getActiveDisplaySets()];\n"

APRES = """    // orthanc-authelia: do not prefetch what has no image.
    //
    // The prefetcher took EVERY display set, including those marked
    // unsupported and the modalities without pixels (SR, SEG, RTSTRUCT...). It
    // then requested an image from a structured report, the server answered
    // 400, and OHIF raised an error banner on a perfectly readable study --
    // at the end of loading, since that is when the prefetcher moves on to the
    // next series.
    const displaySets = [...displaySetService.getActiveDisplaySets()].filter(
      ds => !ds.unsupported && !thumbnailNoImageModalities.includes(ds.Modality)
    );
"""


def main() -> int:
    try:
        source = io.open(CIBLE, encoding="utf-8").read()
    except OSError as err:
        print(f"patch-prefetch-nonimage: {CIBLE} unreadable ({err})", file=sys.stderr)
        return 1

    if APRES in source:
        print("patch-prefetch-nonimage: already applied, nothing to do.")
        return 0

    manquants = [m for m, t in (("import", IMPORT_AVANT), ("filter", AVANT))
                 if t not in source]
    if manquants:
        print(
            "patch-prefetch-nonimage: PATTERN NOT FOUND (" + ", ".join(manquants)
            + ") in " + CIBLE + ".\n"
            "Upstream changed this file. Two possibilities:\n"
            "  - the prefetcher now filters by itself -> delete this script\n"
            "    and its call in the Dockerfile;\n"
            "  - the code merely moved -> port the fix by hand.\n"
            "The build stops here rather than ship a viewer in which the\n"
            "fix would have disappeared silently.",
            file=sys.stderr,
        )
        return 1

    source = source.replace(IMPORT_AVANT, IMPORT_APRES, 1).replace(AVANT, APRES, 1)
    io.open(CIBLE, "w", encoding="utf-8").write(source)
    print("patch-prefetch-nonimage: applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
