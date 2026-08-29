// =============================================================================
// OHIF VIEWER CONFIGURATION FOR ORTHANC-AUTHELIA
// =============================================================================
// Configuration for OHIF v3.10.2 medical imaging viewer
// Optimized for Orthanc PACS integration with French localization

// Extract token from URL if present
const urlParams = new URLSearchParams(window.location.search);
const shareToken = urlParams.get('token');

window.config = {

  // =============================================================================
  // LIBELLES FRANCAIS DE LA LISTE D'ETUDES (necessaire depuis OHIF 3.13)
  // =============================================================================
  // La liste d'etudes a ete reecrite en 3.13 : elle vient desormais de
  // @ohif/ui-next, dont les composants portent leurs libelles en dur, sans
  // aucun appel a t(). Les fichiers de traduction fr/ n'y peuvent rien -- en
  // 3.12 c'etait WorkList.tsx qui appelait t('StudyList:Modality'), ce code
  // n'existe plus.
  //
  // On passe donc par customizationService. On ne REMPLACE pas les colonnes :
  // une colonne fournie en donnee perd ses cellules (jetons de modalite, mise
  // en forme des dates) et redevient du texte brut. L'operateur $set ne
  // reecrit que meta.label et laisse le reste intact.
  //
  // Les index suivent l'ordre de StudyList.defaultColumns :
  //   0 patient  1 mrn  2 studyDateTime  3 modalities
  //   4 description  5 accession  6 instances  7 actions
  //
  // Accents en \uXXXX volontairement : ce fichier est servi sans en-tete de
  // charset, un accent brut ressortirait en mojibake selon le navigateur.
  customizationService: {
    'workList.columns': {
      '0': { meta: { label: { $set: 'Nom du patient' } } },
      '1': { meta: { label: { $set: 'Num\u00e9ro DSN' } } },
      '2': { meta: { label: { $set: 'Date de l\u2019\u00e9tude' } } },
      '3': { meta: { label: { $set: 'Modalit\u00e9' } } },
      '4': { meta: { label: { $set: 'Description' } } },
      '5': { meta: { label: { $set: 'Num\u00e9ro d\u2019acc\u00e8s' } } },
      '6': { meta: { label: { $set: 'Instances' } } },
    },
  },
  // =============================================================================
  // ROUTING & UI CONFIGURATION
  // =============================================================================
  routerBasename: '/ohif',                     // Base URL path for OHIF
  showStudyList: true,                         // Display study list on startup
  useRelativeUrls: true,                       // Use relative URLs for better proxy support
  extensions: [],                              // Additional OHIF extensions (none configured)
  modes: [],                                   // Additional viewing modes (none configured)
  
  // =============================================================================
  // USER EXPERIENCE SETTINGS
  // =============================================================================
  showWarningMessageForCrossOrigin: true,     // Warn about cross-origin issues
  showCPUFallbackMessage: true,               // Show CPU fallback warnings
  showLoadingIndicator: true,                 // Display loading indicators
  experimentalStudyBrowserSort: false,        // Disable experimental sorting
  strictZSpacingForVolumeViewport: true,      // Enforce strict Z-spacing for 3D

  // =============================================================================
  // PERFORMANCE OPTIMIZATION
  // Nom du patient affiche d'emblee dans l'en-tete du visualiseur.
  //
  // Sans ce reglage, OHIF prend « visibleCollapsed » : l'en-tete ne montre
  // qu'une icone, et il faut cliquer dessus pour lire de qui il s'agit. En
  // consultation on veut voir le nom sans rien demander.
  //
  // Valeurs possibles : 'visible' (deplie, repliable au clic),
  // 'visibleCollapsed' (le defaut), 'visibleReadOnly' (deplie et non
  // repliable), 'disabled' (rien du tout).
  showPatientInfo: 'visible',

  // =============================================================================
  // Study prefetching for faster navigation between studies
  // Rallume le 2026-08-29, une fois le prechargeur corrige.
  //
  // Tel que livre par l'amont, StudyPrefetcherService ne filtre RIEN : ni les
  // jeux marques unsupported, ni les modalites sans image (SR, SEG,
  // RTSTRUCT...). Il prechargeait donc un compte rendu structure comme s'il
  // s'agissait d'images, le serveur repondait 400, et OHIF affichait un bandeau
  // d'erreur a la fin du chargement -- sur une etude sur trois ici, 70 series SR
  // pour 209 etudes.
  //
  // Corrige au build par services/ohif/docker/patch-prefetch-nonimage.py, qui
  // filtre la liste avec celle qu'OHIF maintient deja. Le prechargement garde
  // son interet sur les series d'images -- il compte, sur un PACS consulte a
  // travers un tunnel -- et cesse de chercher des pixels la ou il n'y en a pas.
  //
  // Si vous reprenez ce fichier SANS ce correctif, remettez enabled: false.
  studyPrefetcher: {
    enabled: true,                             // Enable study prefetching
    displaySetsCount: 2,                       // Number of display sets to prefetch
    maxNumPrefetchRequests: 10,                // Maximum concurrent prefetch requests
    order: 'closest',                          // Prefetch order strategy
  },

  // =============================================================================
  // INTERNATIONALIZATION (I18N)
  // =============================================================================
  // French as primary language for medical environment
  i18n: {
    defaultLanguage: 'fr',                     // Default language: French
    languages: ['fr', 'en'],                   // Available languages: French, English
    debug: false,                              // Set to true for debugging missing translation keys
    detectLanguage: true                       // Don't auto-detect browser language
  },

  // =============================================================================
  // DICOM DATA SOURCE CONFIGURATION
  // =============================================================================
  defaultDataSourceName: 'dicomweb',          // Default data source name
  
  
  dataSources: [
    {
      // DICOMweb data source for Orthanc PACS integration
      namespace: '@ohif/extension-default.dataSourcesModule.dicomweb',
      sourceName: 'dicomweb',
      configuration: {
        // =============================================================================
        // ORTHANC SERVER INTEGRATION
        // =============================================================================
        friendlyName: 'ORTHANC-AUTHELIA',     // Display name for the PACS server
        name: 'Orthanc',                      // Internal server name
        
        // =============================================================================
        // DICOMWEB API ENDPOINTS
        // =============================================================================
        // These endpoints are proxied through nginx with authentication
        wadoUriRoot: '/wado',                 // WADO-URI endpoint for image retrieval
        qidoRoot: '/dicom-web',               // QIDO-RS endpoint for study/series queries
        wadoRoot: '/dicom-web',               // WADO-RS endpoint for image retrieval
        
        // =============================================================================
        // DICOMWEB PROTOCOL SETTINGS
        // =============================================================================
        qidoSupportsIncludeField: false,      // Orthanc doesn't support includeField parameter
        imageRendering: 'wadors',             // Use WADO-RS for image rendering
        thumbnailRendering: 'wadors',         // Use WADO-RS for thumbnail rendering
        
        // =============================================================================
        // UPLOAD & MULTIPART SETTINGS
        // =============================================================================
        dicomUploadEnabled: true,               // Enable DICOM file upload to PACS
        omitQuotationForMultipartRequest: true, // Orthanc compatibility for multipart requests
        
      },
    },
  ],
};

// Token injection script - runs after OHIF loads
(function() {
  // Wait for OHIF to load
  if (typeof window !== 'undefined') {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    
    if (token) {
      // Override XMLHttpRequest to add token to all requests
      const originalOpen = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function(method, url, async, user, password) {
        // Add token to URL if it doesn't already have one
        if (url && !url.includes('token=') && (url.includes('/dicom-web') || url.includes('/wado'))) {
          // Handle relative URLs properly
          if (url.startsWith('/dicom-web') || url.startsWith('/wado')) {
            const separator = url.includes('?') ? '&' : '?';
            url += separator + 'token=' + token;
          }
        }
        return originalOpen.call(this, method, url, async, user, password);
      };
      
      // Override fetch API as well
      const originalFetch = window.fetch;
      window.fetch = function(url, options) {
        if (typeof url === 'string' && !url.includes('token=') && (url.includes('/dicom-web') || url.includes('/wado'))) {
          // Handle relative URLs properly
          if (url.startsWith('/dicom-web') || url.startsWith('/wado')) {
            const separator = url.includes('?') ? '&' : '?';
            url += separator + 'token=' + token;
          }
        }
        return originalFetch.call(this, url, options);
      };
    }
  }
})();