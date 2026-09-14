// =============================================================================
// OHIF VIEWER CONFIGURATION FOR ORTHANC-AUTHELIA
// =============================================================================
// Configuration for OHIF v3.10.2 medical imaging viewer
// Optimized for Orthanc PACS integration with French localization

// Extract token from URL if present
const urlParams = new URLSearchParams(window.location.search);
const shareToken = urlParams.get('token');

// Display language, resolved the way OHIF's i18next detector does it: ?lng=,
// then the i18next cookie, then localStorage, then the browser. Only the two
// letter code matters here.
function ohifLanguage() {
  let lang = new URLSearchParams(window.location.search).get('lng') || '';
  if (!lang) {
    const cookie = document.cookie.match(/(?:^|;\s*)i18next=([^;]+)/);
    lang = cookie ? decodeURIComponent(cookie[1]) : '';
  }
  if (!lang) {
    try { lang = window.localStorage.getItem('i18nextLng') || ''; } catch (e) { lang = ''; }
  }
  if (!lang) {
    lang = (navigator.languages && navigator.languages[0]) || navigator.language || '';
  }
  return lang.slice(0, 2).toLowerCase();
}

// Study list column labels, per language, in StudyList.defaultColumns order
// (0 patient, 1 mrn, 2 studyDateTime, 3 modalities, 4 description,
// 5 accession, 6 instances). English needs no entry: it is what
// @ohif/ui-next ships. To add a language, add a line.
const STUDY_LIST_LABELS = {
  fr: ['Nom du patient', 'Num\u00e9ro DSN', 'Date de l\u2019\u00e9tude', 'Modalit\u00e9',
       'Description', 'Num\u00e9ro d\u2019acc\u00e8s', 'Instances'],
};
const studyListLabels = STUDY_LIST_LABELS[ohifLanguage()];

window.config = {

  // =============================================================================
  // TRANSLATED LABELS OF THE STUDY LIST (needed since OHIF 3.13)
  // =============================================================================
  // The study list was rewritten in 3.13: it now comes from @ohif/ui-next,
  // whose components carry hard-coded labels, without any call to t(). The
  // fr/ translation files can do nothing about it -- in 3.12 it was
  // WorkList.tsx that called t('StudyList:Modality'), and that code no longer
  // exists.
  //
  // So this goes through customizationService. The columns are NOT replaced:
  // a column supplied as data loses its cells (modality tokens, date
  // formatting) and turns back into plain text. The $set operator only
  // rewrites meta.label and leaves the rest intact.
  //
  // The indexes follow the order of StudyList.defaultColumns:
  //   0 patient  1 mrn  2 studyDateTime  3 modalities
  //   4 description  5 accession  6 instances  7 actions
  //
  // The labels themselves live in STUDY_LIST_LABELS, above window.config,
  // and follow the language OHIF displays in.
  //
  // Accents written as \uXXXX on purpose: this file is served without a
  // charset header, a raw accent would come out as mojibake in some browsers.
  ...(studyListLabels ? {
    customizationService: {
      'workList.columns': Object.fromEntries(
        studyListLabels.map((label, i) => [String(i), { meta: { label: { $set: label } } }])
      ),
    },
  } : {}),
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
  // Patient name shown straight away in the viewer header.
  //
  // Without this setting, OHIF uses "visibleCollapsed": the header only shows
  // an icon, and you have to click it to read who the patient is. When
  // reading a study you want to see the name without asking.
  //
  // Possible values: 'visible' (expanded, collapsible on click),
  // 'visibleCollapsed' (the default), 'visibleReadOnly' (expanded, not
  // collapsible), 'disabled' (nothing at all).
  showPatientInfo: 'visible',

  // =============================================================================
  // Study prefetching for faster navigation between studies
  // Turned back on 2026-08-29, once the prefetcher was fixed.
  //
  // As shipped upstream, StudyPrefetcherService filters NOTHING: neither sets
  // marked unsupported, nor modalities without images (SR, SEG, RTSTRUCT...).
  // It therefore prefetched a structured report as if it were images, the
  // server answered 400, and OHIF showed an error banner at the end of
  // loading -- on one study in three here, 70 SR series for 209 studies.
  //
  // Fixed at build time by services/ohif/docker/patch-prefetch-nonimage.py,
  // which filters the list with the one OHIF already maintains. Prefetching
  // keeps its value on image series -- it matters, on a PACS read through a
  // tunnel -- and stops looking for pixels where there are none.
  //
  // If you reuse this file WITHOUT that fix, set enabled: false again.
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