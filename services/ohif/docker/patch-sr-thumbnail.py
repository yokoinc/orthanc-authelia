#!/usr/bin/env python3
"""Empeche OHIF de reclamer une image aux series qui n'en contiennent pas.

LE PROBLEME
    Une etude qui porte un compte rendu structure -- un Dose Record de scanner,
    par exemple -- affiche un bandeau rouge « Une erreur s'est produite » a
    l'ouverture, alors que les images, elles, s'affichent parfaitement.

    Mesure sur une installation reelle le 2026-08-29 : 70 series SR sur 209
    etudes, soit environ une etude sur trois. Cote serveur, Orthanc repond :

        GET /dicom-web/studies/.../instances/.../frames/1  ->  400
        Bad request: Cannot extract a frame from a DICOM file that does not
        have pixel data.

    Ce qui est la reponse correcte : l'instance est un
    X-Ray Radiation Dose SR (1.2.840.10008.5.1.4.1.1.88.67), il n'y a pas de
    pixels a extraire.

LA CAUSE
    Dans PanelStudyBrowser.tsx, le panneau construit la vignette de CHAQUE jeu
    d'affichage. Or il sait deja qu'il n'affichera pas d'image pour celui-ci :
    getComponentType() rend « thumbnailNoImage » pour les modalites de
    thumbnailNoImageModalities (SR, SEG, RTSTRUCT...) et pour tout jeu marque
    unsupported. Il va simplement chercher le pixel AVANT de s'en apercevoir.

    L'amont a d'ailleurs laisse un TODO juste au-dessus, sur le meme sujet :
    « Is it okay that imageIds are not returned here for SR displaysets? »

LE CORRECTIF
    Sortir de la boucle avant l'appel reseau, sur la meme condition que celle
    qui decide plus bas de ne pas afficher d'image. Sept lignes, aucune
    dependance nouvelle : thumbnailNoImageModalities est deja importe dans ce
    fichier.

ENTRETIEN
    A REVERIFIER A CHAQUE MONTEE DE VERSION D'OHIF. Le script echoue
    volontairement si le motif a disparu -- le build s'arrete alors, plutot que
    de produire une image ou le correctif serait passe a la trappe en silence.
    Si l'amont corrige le probleme, supprimer ce fichier et son appel dans le
    Dockerfile.
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

          // orthanc-authelia : ne pas reclamer d'image a un jeu qui n'en a pas.
          // getComponentType() rend deja une vignette SANS image pour ces
          // modalites et pour les jeux non supportes -- mais le pixel etait
          // demande avant, et le serveur repond 400 sur un compte rendu
          // structure. OHIF le remontait en bandeau d'erreur, sur une etude
          // par ailleurs parfaitement lisible.
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
        print(f"patch-sr-thumbnail : {CIBLE} illisible ({err})", file=sys.stderr)
        return 1

    if APRES in source:
        print("patch-sr-thumbnail : deja applique, rien a faire.")
        return 0

    if AVANT not in source:
        print(
            "patch-sr-thumbnail : MOTIF INTROUVABLE dans " + CIBLE + ".\n"
            "L'amont a modifie ce fichier. Deux possibilites :\n"
            "  - le probleme est corrige en amont -> supprimer ce script et son\n"
            "    appel dans le Dockerfile ;\n"
            "  - le code a seulement bouge -> reporter le correctif a la main.\n"
            "Le build s'arrete ici plutot que de livrer un viewer ou le\n"
            "correctif aurait disparu sans bruit.",
            file=sys.stderr,
        )
        return 1

    io.open(CIBLE, "w", encoding="utf-8").write(source.replace(AVANT, APRES, 1))
    print("patch-sr-thumbnail : applique.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
