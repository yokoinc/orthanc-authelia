#!/usr/bin/env python3
"""Stop OHIF from requesting an image from series that contain none.

THE PROBLEM
    A study carrying a structured report -- a CT Dose Record, for example --
    shows a red "An error occurred" banner when opened, while the images
    themselves display perfectly.

    Measured on a real installation on 2026-08-29: 70 SR series across 209
    studies, roughly one study in three. On the server side, Orthanc answers:

        GET /dicom-web/studies/.../instances/.../frames/1  ->  400
        Bad request: Cannot extract a frame from a DICOM file that does not
        have pixel data.

    Which is the correct answer: the instance is an
    X-Ray Radiation Dose SR (1.2.840.10008.5.1.4.1.1.88.67), there are no
    pixels to extract.

THE CAUSE
    In PanelStudyBrowser.tsx, the panel builds the thumbnail of EVERY display
    set. Yet it already knows it will not show an image for this one:
    getComponentType() returns "thumbnailNoImage" for the modalities in
    thumbnailNoImageModalities (SR, SEG, RTSTRUCT...) and for any display set
    marked unsupported. It simply fetches the pixel BEFORE noticing.

    Upstream even left a TODO right above, on the same subject:
    "Is it okay that imageIds are not returned here for SR displaysets?"

THE FIX
    Leave the loop before the network call, on the same condition as the one
    that decides further down not to show an image. Seven lines, no new
    dependency: thumbnailNoImageModalities is already imported in this file.

MAINTENANCE
    TO RECHECK ON EVERY OHIF UPGRADE. The script fails on purpose if the pattern
    has disappeared -- the build then stops, rather than producing an image in
    which the fix silently went missing. If upstream fixes the problem, delete
    this file and its call in the Dockerfile.
"""
import io
import sys

CIBLE = "extensions/default/src/Panels/StudyBrowser/PanelStudyBrowser.tsx"

AVANT = """          // TODO: Is it okay that imageIds are not returned here for SR displaysets?
          if (!imageId) {
            return;
          }
"""

APRES = """          // TODO: Is it okay that imageIds are not returned here for SR displaysets?
          if (!imageId) {
            return;
          }

          // orthanc-authelia: do not request an image from a set that has none.
          // getComponentType() already returns a thumbnail WITHOUT an image for
          // these modalities and for unsupported sets -- but the pixel was
          // requested first, and the server answers 400 on a structured
          // report. OHIF surfaced it as an error banner, on a study that was
          // otherwise perfectly readable.
          if (
            displaySet.unsupported ||
            thumbnailNoImageModalities.includes(displaySet.Modality)
          ) {
            return;
          }
"""


def main() -> int:
    try:
        source = io.open(CIBLE, encoding="utf-8").read()
    except OSError as err:
        print(f"patch-sr-thumbnail: {CIBLE} unreadable ({err})", file=sys.stderr)
        return 1

    if APRES in source:
        print("patch-sr-thumbnail: already applied, nothing to do.")
        return 0

    if AVANT not in source:
        print(
            "patch-sr-thumbnail: PATTERN NOT FOUND in " + CIBLE + ".\n"
            "Upstream changed this file. Two possibilities:\n"
            "  - the problem is fixed upstream -> delete this script and its\n"
            "    call in the Dockerfile;\n"
            "  - the code merely moved -> port the fix by hand.\n"
            "The build stops here rather than ship a viewer in which the\n"
            "fix would have disappeared silently.",
            file=sys.stderr,
        )
        return 1

    io.open(CIBLE, "w", encoding="utf-8").write(source.replace(AVANT, APRES, 1))
    print("patch-sr-thumbnail: applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
