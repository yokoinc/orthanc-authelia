// Regroupement et libelles des parametres Orthanc editables.
//
// L'API renvoie un dictionnaire plat de 43 cles techniques. Affichees telles
// quelles a la suite, elles n'apprennent rien : "StableAge" ou
// "IngestTranscoding" ne se devinent pas. Ce fichier ne fait que decrire --
// aucune logique -- pour que l'onglet reste lisible et que l'ajout d'un
// parametre se limite a une ligne ici.
//
// Les cles absentes de cette description restent affichees, dans un groupe
// « Autres » : mieux vaut un champ mal range qu'un champ invisible.

export const GROUPES = [
  {
    id: 'identite',
    titre: 'Identité',
    icone: 'fa-id-card',
    champs: {
      Name: ['Nom du serveur', "Affiché dans l'interface et annoncé aux autres équipements."],
      DicomAet: ['Titre AE DICOM', "Nom du nœud sur le réseau DICOM. Seize caractères au maximum, c'est la norme qui l'impose."],
    },
  },
  {
    id: 'dicom',
    titre: 'Réseau DICOM',
    icone: 'fa-network-wired',
    champs: {
      DicomServerEnabled: ['Activer le serveur DICOM', "Réception des examens envoyés par les modalités (protocole DIMSE, port dédié)."],
      DicomPort: ['Port DICOM', 'Port d\'écoute DIMSE. 4242 par convention.'],
      DicomCheckCalledAet: ['Vérifier le titre AE appelé', "Refuse les connexions qui ne s'adressent pas explicitement à ce serveur."],
      DicomAlwaysAllowEcho: ['Autoriser les tests d\'écho', "Répond aux vérifications de connectivité (C-ECHO), même d'un équipement inconnu."],
      DicomAlwaysAllowStore: ['Autoriser l\'envoi d\'examens', "Accepte les examens (C-STORE) d'équipements non déclarés. À laisser désactivé si les modalités sont toutes connues."],
      DicomAlwaysAllowFind: ['Autoriser les recherches', 'Répond aux requêtes de recherche (C-FIND) d\'équipements non déclarés.'],
      DicomAlwaysAllowMove: ['Autoriser les transferts', 'Répond aux demandes de transfert (C-MOVE) d\'équipements non déclarés.'],
      DicomScpTimeout: ['Délai d\'attente (s)', 'Abandon d\'une association DICOM restée sans réponse.'],
      DicomThreadsCount: ['Connexions simultanées', 'Nombre d\'associations DICOM traitées en parallèle.'],
      SynchronousCMove: ['Transferts synchrones', "Attend la fin du transfert avant de répondre, au lieu de le traiter en tâche de fond."],
      DicomModalitiesInDatabase: ['Modalités en base', "Enregistre les équipements déclarés en base plutôt que dans le fichier de configuration : ils survivent alors à une réécriture de celui-ci."],
      OrthancPeersInDatabase: ['Serveurs pairs en base', 'Même principe pour les autres serveurs Orthanc déclarés.'],
    },
  },
  {
    id: 'http',
    titre: 'Accès web',
    icone: 'fa-globe',
    champs: {
      RemoteAccessAllowed: ['Autoriser l\'accès distant', "Sans cela, seul l'hôte local peut joindre l'interface. Le proxy étant dans un autre conteneur, désactiver cette option coupe tout accès."],
      HttpPort: ['Port HTTP', "Port interne d'Orthanc. Le proxy s'y connecte ; le changer impose d'adapter sa configuration."],
      HttpTimeout: ['Délai d\'attente HTTP (s)', 'Abandon d\'une requête web restée sans réponse.'],
      HttpCompressionEnabled: ['Compression HTTP', 'Compresse les réponses. À laisser actif sauf réseau très rapide et processeur limité.'],
    },
  },
  {
    id: 'stockage',
    titre: 'Stockage',
    icone: 'fa-database',
    champs: {
      StorageCompression: ['Compresser les fichiers', "Réduit la place occupée au prix de temps processeur à chaque lecture et écriture."],
      MaximumStorageSize: ['Taille maximale (Mo)', '0 pour ne pas limiter. Au-delà, le comportement dépend du mode ci-dessous.'],
      MaximumPatientCount: ['Nombre maximal de patients', '0 pour ne pas limiter.'],
      MaximumStorageMode: ['Mode de dépassement', "Recycle supprime les examens les plus anciens ; Reject refuse les nouveaux."],
      StoreMD5ForAttachments: ['Empreinte des fichiers', "Calcule une empreinte à l'écriture pour détecter une corruption ultérieure."],
      OverwriteInstances: ['Écraser les doublons', "Comportement lorsqu'un examen déjà présent est renvoyé."],
      StableAge: ['Délai de stabilité (s)', "Durée sans nouvelle image après laquelle un examen est considéré comme complet. Les traitements automatiques s'en servent."],
    },
  },
  {
    id: 'dicomweb',
    titre: 'DICOMweb',
    icone: 'fa-share-nodes',
    champs: {
      'DicomWeb.Enable': ['Activer DICOMweb', "Protocole utilisé par les visionneuses web (OHIF, Stone). Le désactiver les empêche d'afficher les examens."],
      'DicomWeb.Root': ['Chemin interne', "Racine de l'API DICOMweb côté Orthanc."],
      'DicomWeb.PublicRoot': ['Chemin public', "Racine annoncée aux clients, telle qu'elle apparaît derrière le proxy."],
      'DicomWeb.EnableWado': ['Activer WADO', 'Ancien protocole de récupération, encore attendu par certains outils.'],
      'DicomWeb.EnableMetadata': ['Exposer les métadonnées', 'Permet aux visionneuses de lire les tags sans télécharger les images.'],
      'DicomWeb.StowMaxInstances': ['Images par envoi', "Nombre maximal d'images acceptées en un seul envoi DICOMweb. 0 pour ne pas limiter."],
      'DicomWeb.StowMaxSize': ['Taille par envoi (Mo)', '0 pour ne pas limiter.'],
    },
  },
  {
    id: 'traitement',
    titre: 'Traitement des images',
    icone: 'fa-wand-magic-sparkles',
    champs: {
      IngestTranscoding: ['Recompression à la réception', "Convertit les images entrantes dans une syntaxe de transfert donnée. Laisser vide pour conserver le format d'origine."],
      IngestTranscodingOfUncompressed: ['Recompresser aussi le non compressé', "N'a d'effet que si une recompression est configurée ci-dessus."],
      DefaultEncoding: ['Encodage par défaut', "Jeu de caractères supposé quand un examen ne le précise pas. Latin1 en Europe de l'Ouest."],
      AcceptedTransferSyntaxes: ['Syntaxes acceptées', "Formats d'image acceptés à la réception. Une valeur par ligne."],
    },
  },
  {
    id: 'taches',
    titre: 'Tâches',
    icone: 'fa-list-check',
    champs: {
      ConcurrentJobs: ['Tâches simultanées', 'Nombre de traitements exécutés en parallèle (envois, exports, anonymisations).'],
      JobsHistorySize: ['Historique conservé', 'Nombre de tâches terminées gardées en mémoire.'],
      SaveJobs: ['Conserver après redémarrage', 'Les tâches inachevées reprennent au démarrage suivant.'],
    },
  },
  {
    id: 'recherche',
    titre: 'Recherches',
    icone: 'fa-magnifying-glass',
    champs: {
      LimitFindResults: ['Résultats maximum', "Plafond du nombre d'examens renvoyés par une recherche. 0 pour ne pas limiter."],
      LimitFindInstances: ['Images maximum', 'Même plafond au niveau des images.'],
    },
  },
  {
    id: 'journaux',
    titre: 'Journaux',
    icone: 'fa-file-lines',
    champs: {
      LogLevel: ['Niveau de détail', 'default, verbose ou trace. Les niveaux élevés produisent beaucoup de volume.'],
      DeidentifyLogs: ['Masquer les données patient', "Retire les identifiants patient des journaux. À laisser actif : les journaux sont souvent transmis lors d'un dépannage."],
    },
  },
]
