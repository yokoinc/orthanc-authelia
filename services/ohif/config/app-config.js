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
  // =============================================================================
  // Study prefetching for faster navigation between studies
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