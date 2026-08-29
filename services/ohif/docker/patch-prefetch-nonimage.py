#!/usr/bin/env python3
"""Empeche le prechargeur d'OHIF de precharger des series sans image.

LE PROBLEME
    Une etude portant un compte rendu structure -- un Dose Record de scanner --
    affichait un bandeau rouge « Une erreur s'est produite » A LA FIN du
    chargement des images, alors que celles-ci s'affichaient parfaitement.

    Mesure sur une installation reelle le 2026-08-29 : 70 series SR sur 209
    etudes, soit environ une etude sur trois. Cote serveur :

        GET /dicom-web/studies/.../instances/.../frames/1  ->  400
        Bad request: Cannot extract a frame from a DICOM file that does not
        have pixel data.

    Reponse correcte d'Orthanc : l'instance est un X-Ray Radiation Dose SR
    (1.2.840.10008.5.1.4.1.1.88.67), il n'y a pas de pixels a extraire.

LA CAUSE
    StudyPrefetcherService prend `displaySetService.getActiveDisplaySets()` et
    precharge les suivants des que la serie active est chargee -- d'ou un
    declenchement A LA FIN du chargement, signature qui a permis de l'isoler.

    Il ne filtre RIEN : ni les jeux marques `unsupported`, ni les modalites
    connues pour n'avoir aucune image. OHIF maintient pourtant la liste
    (thumbnailNoImageModalities : SR, SEG, RTSTRUCT, RTPLAN, RTDOSE, DOC,
    PMAP, RWV) et s'en sert ailleurs, dans le panneau d'etudes.

    A noter : l'amont n'active pas ce service par defaut. C'est notre
    configuration qui l'allume, pour accelerer le passage d'une serie a
    l'autre -- ce qui compte sur un PACS consulte a travers un tunnel.

LE CORRECTIF
    Filtrer la liste a la source, avec la propre liste d'OHIF. Le prechargement
    garde tout son interet sur les series d'images, et cesse d'aller chercher
    des pixels la ou il n'y en a pas.

ENTRETIEN
    A REVERIFIER A CHAQUE MONTEE DE VERSION D'OHIF. Le script echoue
    volontairement si le motif a disparu -- le build s'arrete alors, plutot que
    de produire une image ou le correctif serait passe a la trappe en silence.
    Si l'amont ajoute ce filtre, supprimer ce fichier et son appel dans le
    Dockerfile.
"""
import io
import sys

CIBLE = "platform/core/src/services/StudyPrefetcherService/StudyPrefetcherService.ts"

IMPORT_AVANT = "import { DisplaySet } from '../../types';\n"
IMPORT_APRES = (
    "import { DisplaySet } from '../../types';\n"
    "// orthanc-authelia : liste maintenue par OHIF des modalites sans image.\n"
    "import { thumbnailNoImageModalities } from '../../utils/thumbnailNoImageModalities';\n"
)

AVANT = "    const displaySets = [...displaySetService.getActiveDisplaySets()];\n"

APRES = """    // orthanc-authelia : ne pas precharger ce qui n'a pas d'image.
    //
    // Le prechargeur prenait TOUS les jeux d'affichage, y compris ceux marques
    // unsupported et les modalites sans pixel (SR, SEG, RTSTRUCT...). Il
    // reclamait alors une image a un compte rendu structure, le serveur
    // repondait 400, et OHIF remontait un bandeau d'erreur sur une etude
    // parfaitement lisible -- a la fin du chargement, puisque c'est la que le
    // prechargeur passe a la serie suivante.
    const displaySets = [...displaySetService.getActiveDisplaySets()].filter(
      ds => !ds.unsupported && !thumbnailNoImageModalities.includes(ds.Modality)
    );
"""


def main() -> int:
    try:
        source = io.open(CIBLE, encoding="utf-8").read()
    except OSError as err:
        print(f"patch-prefetch-nonimage : {CIBLE} illisible ({err})", file=sys.stderr)
        return 1

    if APRES in source:
        print("patch-prefetch-nonimage : deja applique, rien a faire.")
        return 0

    manquants = [m for m, t in (("import", IMPORT_AVANT), ("filtre", AVANT))
                 if t not in source]
    if manquants:
        print(
            "patch-prefetch-nonimage : MOTIF INTROUVABLE (" + ", ".join(manquants)
            + ") dans " + CIBLE + ".\n"
            "L'amont a modifie ce fichier. Deux possibilites :\n"
            "  - le prechargeur filtre desormais lui-meme -> supprimer ce script\n"
            "    et son appel dans le Dockerfile ;\n"
            "  - le code a seulement bouge -> reporter le correctif a la main.\n"
            "Le build s'arrete ici plutot que de livrer un viewer ou le\n"
            "correctif aurait disparu sans bruit.",
            file=sys.stderr,
        )
        return 1

    source = source.replace(IMPORT_AVANT, IMPORT_APRES, 1).replace(AVANT, APRES, 1)
    io.open(CIBLE, "w", encoding="utf-8").write(source)
    print("patch-prefetch-nonimage : applique.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
