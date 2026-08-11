import { t } from './i18n.js'

// Grouping and labels for the editable Orthanc settings.
//
// The API returns a flat dictionary of some sixty technical keys. Listed
// as-is one after the other they teach nothing: "StableAge" and
// "IngestTranscoding" cannot be guessed. This file only describes -- no
// logic -- so the tab stays readable and adding a setting amounts to one
// line here.
//
// Keys absent from this description stay visible, in an "Other" group: a
// misfiled field beats an invisible one.

export const GROUPES = [
  {
    id: 'identite',
    titre: 'Identité',
    icone: 'fa-id-card',
    champs: {
      Name: [t('cfg_Name_label', 'Nom du serveur'),
          t('cfg_Name_help', 'Affiché dans l\'interface et annoncé aux autres équipements.')],

      DicomAet: [t('cfg_DicomAet_label', 'Titre AE DICOM'),
          t('cfg_DicomAet_help', 'Nom du nœud sur le réseau DICOM. Seize caractères au maximum, c\'est la norme qui l\'impose.')],

    },
  },
  {
    id: 'dicom',
    titre: 'Réseau DICOM',
    icone: 'fa-network-wired',
    champs: {
      DicomServerEnabled: [t('cfg_DicomServerEnabled_label', 'Activer le serveur DICOM'),
          t('cfg_DicomServerEnabled_help', 'Réception des examens envoyés par les modalités (protocole DIMSE, port dédié).')],

      DicomPort: [t('cfg_DicomPort_label', 'Port DICOM'),
          t('cfg_DicomPort_help', 'Port d\'écoute DIMSE. 4242 par convention.')],

      DicomCheckCalledAet: [t('cfg_DicomCheckCalledAet_label', 'Vérifier le titre AE appelé'),
          t('cfg_DicomCheckCalledAet_help', 'Refuse les connexions qui ne s\'adressent pas explicitement à ce serveur.')],

      DicomAlwaysAllowEcho: [t('cfg_DicomAlwaysAllowEcho_label', 'Autoriser les tests d\'écho'),
          t('cfg_DicomAlwaysAllowEcho_help', 'Répond aux vérifications de connectivité (C-ECHO), même d\'un équipement inconnu.')],

      DicomAlwaysAllowStore: [t('cfg_DicomAlwaysAllowStore_label', 'Autoriser l\'envoi d\'examens'),
          t('cfg_DicomAlwaysAllowStore_help', 'Accepte les examens (C-STORE) d\'équipements non déclarés. À laisser désactivé si les modalités sont toutes connues.')],

      DicomAlwaysAllowFind: [t('cfg_DicomAlwaysAllowFind_label', 'Autoriser les recherches'),
          t('cfg_DicomAlwaysAllowFind_help', 'Répond aux requêtes de recherche (C-FIND) d\'équipements non déclarés.')],

      DicomAlwaysAllowMove: [t('cfg_DicomAlwaysAllowMove_label', 'Autoriser les transferts'),
          t('cfg_DicomAlwaysAllowMove_help', 'Répond aux demandes de transfert (C-MOVE) d\'équipements non déclarés.')],

      DicomScpTimeout: [t('cfg_DicomScpTimeout_label', 'Délai d\'attente (s)'),
          t('cfg_DicomScpTimeout_help', 'Abandon d\'une association DICOM restée sans réponse.')],

      DicomThreadsCount: [t('cfg_DicomThreadsCount_label', 'Connexions simultanées'),
          t('cfg_DicomThreadsCount_help', 'Nombre d\'associations DICOM traitées en parallèle.')],

      SynchronousCMove: [t('cfg_SynchronousCMove_label', 'Transferts synchrones'),
          t('cfg_SynchronousCMove_help', 'Attend la fin du transfert avant de répondre, au lieu de le traiter en tâche de fond.')],

      DicomModalitiesInDatabase: [t('cfg_DicomModalitiesInDatabase_label', 'Modalités en base'),
          t('cfg_DicomModalitiesInDatabase_help', 'Enregistre les équipements déclarés en base plutôt que dans le fichier de configuration : ils survivent alors à une réécriture de celui-ci.')],

      OrthancPeersInDatabase: [t('cfg_OrthancPeersInDatabase_label', 'Serveurs pairs en base'),
          t('cfg_OrthancPeersInDatabase_help', 'Même principe pour les autres serveurs Orthanc déclarés.')],

    },
  },
  {
    id: 'http',
    titre: 'Accès web',
    icone: 'fa-globe',
    champs: {
      RemoteAccessAllowed: [t('cfg_RemoteAccessAllowed_label', 'Autoriser l\'accès distant'),
          t('cfg_RemoteAccessAllowed_help', 'Sans cela, seul l\'hôte local peut joindre l\'interface. Le proxy étant dans un autre conteneur, désactiver cette option coupe tout accès.')],

      HttpPort: [t('cfg_HttpPort_label', 'Port HTTP'),
          t('cfg_HttpPort_help', 'Port interne d\'Orthanc. Le proxy s\'y connecte ; le changer impose d\'adapter sa configuration.')],

      HttpTimeout: [t('cfg_HttpTimeout_label', 'Délai d\'attente HTTP (s)'),
          t('cfg_HttpTimeout_help', 'Abandon d\'une requête web restée sans réponse.')],

      HttpCompressionEnabled: [t('cfg_HttpCompressionEnabled_label', 'Compression HTTP'),
          t('cfg_HttpCompressionEnabled_help', 'Compresse les réponses. À laisser actif sauf réseau très rapide et processeur limité.')],

    },
  },
  {
    id: 'stockage',
    titre: 'Stockage',
    icone: 'fa-database',
    champs: {
      StorageCompression: [t('cfg_StorageCompression_label', 'Compresser les fichiers'),
          t('cfg_StorageCompression_help', 'Réduit la place occupée au prix de temps processeur à chaque lecture et écriture.')],

      MaximumStorageSize: [t('cfg_MaximumStorageSize_label', 'Taille maximale (Mo)'),
          t('cfg_MaximumStorageSize_help', '0 pour ne pas limiter. Au-delà, le comportement dépend du mode ci-dessous.')],

      MaximumPatientCount: [t('cfg_MaximumPatientCount_label', 'Nombre maximal de patients'),
          t('cfg_MaximumPatientCount_help', '0 pour ne pas limiter.')],

      MaximumStorageMode: [t('cfg_MaximumStorageMode_label', 'Mode de dépassement'),
          t('cfg_MaximumStorageMode_help', 'Recycle supprime les examens les plus anciens ; Reject refuse les nouveaux.')],

      StoreMD5ForAttachments: [t('cfg_StoreMD5ForAttachments_label', 'Empreinte des fichiers'),
          t('cfg_StoreMD5ForAttachments_help', 'Calcule une empreinte à l\'écriture pour détecter une corruption ultérieure.')],
      OverwriteInstances: [t('cfg_OverwriteInstances_label', 'Écraser les doublons'),
          t('cfg_OverwriteInstances_help', 'Comportement lorsqu\'un examen déjà présent est renvoyé.')],

      StableAge: [t('cfg_StableAge_label', 'Délai de stabilité (s)'),
          t('cfg_StableAge_help', 'Durée sans nouvelle image après laquelle un examen est considéré comme complet. Les traitements automatiques s\'en servent.')],

    },
  },
  {
    id: 'dicomweb',
    titre: 'DICOMweb',
    icone: 'fa-share-nodes',
    champs: {
      'DicomWeb.Enable': [t('cfg_DicomWeb_Enable_label', 'Activer DICOMweb'),
          t('cfg_DicomWeb_Enable_help', 'Protocole utilisé par les visionneuses web (OHIF, Stone). Le désactiver les empêche d\'afficher les examens.')],

      'DicomWeb.Root': [t('cfg_DicomWeb_Root_label', 'Chemin interne'),
          t('cfg_DicomWeb_Root_help', 'Racine de l\'API DICOMweb côté Orthanc.')],

      'DicomWeb.PublicRoot': [t('cfg_DicomWeb_PublicRoot_label', 'Chemin public'),
          t('cfg_DicomWeb_PublicRoot_help', 'Racine annoncée aux clients, telle qu\'elle apparaît derrière le proxy.')],

      'DicomWeb.EnableWado': [t('cfg_DicomWeb_EnableWado_label', 'Activer WADO'),
          t('cfg_DicomWeb_EnableWado_help', 'Ancien protocole de récupération, encore attendu par certains outils.')],

      'DicomWeb.EnableMetadata': [t('cfg_DicomWeb_EnableMetadata_label', 'Exposer les métadonnées'),
          t('cfg_DicomWeb_EnableMetadata_help', 'Permet aux visionneuses de lire les tags sans télécharger les images.')],

      'DicomWeb.StowMaxInstances': [t('cfg_DicomWeb_StowMaxInstances_label', 'Images par envoi'),
          t('cfg_DicomWeb_StowMaxInstances_help', 'Nombre maximal d\'images acceptées en un seul envoi DICOMweb. 0 pour ne pas limiter.')],

      'DicomWeb.StowMaxSize': [t('cfg_DicomWeb_StowMaxSize_label', 'Taille par envoi (Mo)'),
          t('cfg_DicomWeb_StowMaxSize_help', '0 pour ne pas limiter.')],

    },
  },
  {
    id: 'traitement',
    titre: 'Traitement des images',
    icone: 'fa-wand-magic-sparkles',
    champs: {
      IngestTranscoding: [t('cfg_IngestTranscoding_label', 'Recompression à la réception'),
          t('cfg_IngestTranscoding_help', 'Convertit les images entrantes dans une syntaxe de transfert donnée. Laisser vide pour conserver le format d\'origine.')],

      IngestTranscodingOfUncompressed: [t('cfg_IngestTranscodingOfUncompressed_label', 'Recompresser aussi le non compressé'),
          t('cfg_IngestTranscodingOfUncompressed_help', 'N\'a d\'effet que si une recompression est configurée ci-dessus.')],

      DefaultEncoding: [t('cfg_DefaultEncoding_label', 'Encodage par défaut'),
          t('cfg_DefaultEncoding_help', 'Jeu de caractères supposé quand un examen ne le précise pas. Latin1 en Europe de l\'Ouest.')],

      AcceptedTransferSyntaxes: [t('cfg_AcceptedTransferSyntaxes_label', 'Syntaxes acceptées'),
          t('cfg_AcceptedTransferSyntaxes_help', 'Formats d\'image acceptés à la réception. Une valeur par ligne.')],

    },
  },
  {
    id: 'taches',
    titre: 'Tâches',
    icone: 'fa-list-check',
    champs: {
      ConcurrentJobs: [t('cfg_ConcurrentJobs_label', 'Tâches simultanées'),
          t('cfg_ConcurrentJobs_help', 'Nombre de traitements exécutés en parallèle (envois, exports, anonymisations).')],

      JobsHistorySize: [t('cfg_JobsHistorySize_label', 'Historique conservé'),
          t('cfg_JobsHistorySize_help', 'Nombre de tâches terminées gardées en mémoire.')],

      SaveJobs: [t('cfg_SaveJobs_label', 'Conserver après redémarrage'),
          t('cfg_SaveJobs_help', 'Les tâches inachevées reprennent au démarrage suivant.')],

    },
  },
  {
    id: 'recherche',
    titre: 'Recherches',
    icone: 'fa-magnifying-glass',
    champs: {
      LimitFindResults: [t('cfg_LimitFindResults_label', 'Résultats maximum'),
          t('cfg_LimitFindResults_help', 'Plafond du nombre d\'examens renvoyés par une recherche. 0 pour ne pas limiter.')],

      LimitFindInstances: [t('cfg_LimitFindInstances_label', 'Images maximum'),
          t('cfg_LimitFindInstances_help', 'Même plafond au niveau des images.')],

    },
  },
  {
    id: 'entretien',
    titre: 'Entretien automatique',
    icone: 'fa-broom',
    champs: {
      'Housekeeper.Enable': [t('cfg_Housekeeper_Enable_label', 'Activer l\'entretien'),
          t('cfg_Housekeeper_Enable_help', 'Tache de fond qui remet la base en cohérence après un changement de configuration : recompression du stockage, tags principaux, cache DICOMweb.')],
      'Housekeeper.ThrottleDelay': [t('cfg_Housekeeper_ThrottleDelay_label', 'Ménagement (ms)'),
          t('cfg_Housekeeper_ThrottleDelay_help', 'Pause entre deux traitements. Plus la valeur est élevée, moins le serveur est sollicité, plus l\'entretien est long.')],
      'Housekeeper.Force': [t('cfg_Housekeeper_Force_label', 'Forcer un passage complet'),
          t('cfg_Housekeeper_Force_help', 'Retraite toute la base au prochain démarrage, même sans changement détecté. À laisser désactivé en usage courant.')],
      'Housekeeper.Triggers.StorageCompressionChange': [t('cfg_Housekeeper_StorageCompressionChange_label', 'Sur changement de compression'),
          t('cfg_Housekeeper_StorageCompressionChange_help', 'Recompresse les fichiers déjà stockés quand le réglage de compression change.')],
      'Housekeeper.Triggers.MainDicomTagsChange': [t('cfg_Housekeeper_MainDicomTagsChange_label', 'Sur changement de tags'),
          t('cfg_Housekeeper_MainDicomTagsChange_help', 'Reconstruit les tags indexés quand la liste des tags principaux change.')],
      'Housekeeper.Triggers.UnnecessaryDicomAsJsonFiles': [t('cfg_Housekeeper_UnnecessaryDicomAsJsonFiles_label', 'Nettoyer les fichiers JSON'),
          t('cfg_Housekeeper_UnnecessaryDicomAsJsonFiles_help', 'Supprime les copies JSON devenues inutiles des examens.')],
      'Housekeeper.Triggers.DicomWebCache': [t('cfg_Housekeeper_DicomWebCache_label', 'Reconstruire le cache DICOMweb'),
          t('cfg_Housekeeper_DicomWebCache_help', 'Régénère les métadonnées que les visionneuses lisent sans télécharger les images.')],
    },
  },
  {
    id: 'interface',
    titre: 'Interface et partage',
    icone: 'fa-display',
    champs: {
      'OrthancExplorer2.Theme': [t('cfg_Theme_label', 'Thème'),
          t('cfg_Theme_help', 'Clair ou sombre. Le sombre fatigue moins les yeux en salle de lecture.')],

      'OrthancExplorer2.UiOptions.ShowOrthancName': [t('cfg_ShowOrthancName_label', 'Afficher le nom du serveur'),
          t('cfg_ShowOrthancName_help', 'Utile quand on utilise plusieurs PACS : on voit d\'un coup d\'œil sur lequel on travaille.')],

      'OrthancExplorer2.UiOptions.EnableViewerQuickButton': [t('cfg_EnableViewerQuickButton_label', 'Bouton d\'ouverture rapide'),
          t('cfg_EnableViewerQuickButton_help', 'Ouvre un examen dans la visionneuse en un clic depuis la liste.')],

      'OrthancExplorer2.EnableReportQuickButton': [t('cfg_EnableReportQuickButton_label', 'Bouton compte rendu'),
          t('cfg_EnableReportQuickButton_help', 'Raccourci vers le compte rendu associé à l\'examen, lorsqu\'il existe.')],

      'OrthancExplorer2.UiOptions.EnableOpenInOhifViewer3': [t('cfg_EnableOhif_label', 'Proposer OHIF'),
          t('cfg_EnableOhif_help', 'Visionneuse polyvalente, adaptée à la plupart des examens.')],

      'OrthancExplorer2.UiOptions.EnableOpenInStoneWebViewer': [t('cfg_EnableStone_label', 'Proposer Stone Web Viewer'),
          t('cfg_EnableStone_help', 'Visionneuse légère, rapide à ouvrir sur une connexion lente.')],

      'OrthancExplorer2.UiOptions.EnableOpenInVolView': [t('cfg_EnableVolView_label', 'Proposer VolView'),
          t('cfg_EnableVolView_help', 'Rendu volumique 3D, pour les scanners et IRM.')],

      'OrthancExplorer2.UiOptions.EnableShares': [t('cfg_EnableShares_label', 'Autoriser les liens de partage'),
          t('cfg_EnableShares_help', 'Permet d\'envoyer un examen à un confrère par un lien, sans lui créer de compte. Désactiver retire la fonction de l\'interface.')],

      'OrthancExplorer2.Tokens.ShareType': [t('cfg_ShareType_label', 'Visionneuse des liens de partage'),
          t('cfg_ShareType_help', 'Celle proposée par défaut au moment de créer un lien. Reste modifiable à chaque partage.')],

      'OrthancExplorer2.UiOptions.DefaultShareDuration': [t('cfg_DefaultShareDuration_label', 'Durée d\'un lien de partage (jours)'),
          t('cfg_DefaultShareDuration_help', 'Validité proposée par défaut. 0 pour un lien sans date d\'expiration.')],

      'OrthancExplorer2.Tokens.InstantLinksValidity': [t('cfg_InstantLinksValidity_label', 'Validité d\'un lien instantané (secondes)'),
          t('cfg_InstantLinksValidity_help', 'Durée de vie du lien ouvert directement depuis l\'interface. Quelques minutes suffisent.')],

      'OrthancExplorer2.UiOptions.ShareDurations': [t('cfg_ShareDurations_label', 'Durées proposées (jours)'),
          t('cfg_ShareDurations_help', 'Choix offerts au moment de créer un lien, une valeur par ligne. 0 signifie sans expiration.')],

      'OrthancExplorer2.UiOptions.StudyListColumns': [t('cfg_StudyListColumns_label', 'Colonnes de la liste d\'examens'),
          t('cfg_StudyListColumns_help', 'Une par ligne, dans l\'ordre d\'affichage. Courantes : PatientID, PatientName, StudyDate, StudyDescription, AccessionNumber, InstitutionName, Modality.')],

      'OrthancExplorer2.UiOptions.ViewersOrdering': [t('cfg_ViewersOrdering_label', 'Ordre des visionneuses'),
          t('cfg_ViewersOrdering_help', 'Ordre d\'apparition dans les menus, une par ligne. Chez nous : ohif, stone-webviewer, volview.')],

    },
  },
  {
    id: 'journaux',
    titre: 'Journaux',
    icone: 'fa-file-lines',
    champs: {
      LogLevel: [t('cfg_LogLevel_label', 'Niveau de détail'),
          t('cfg_LogLevel_help', 'default, verbose ou trace. Les niveaux élevés produisent beaucoup de volume.')],

      DeidentifyLogs: [t('cfg_DeidentifyLogs_label', 'Masquer les données patient'),
          t('cfg_DeidentifyLogs_help', 'Retire les identifiants patient des journaux. À laisser actif : les journaux sont souvent transmis lors d\'un dépannage.')],

    },
  },
]
