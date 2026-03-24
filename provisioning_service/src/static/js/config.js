// Configuration
// Values are injected at build/deploy time or loaded from /api/config endpoint.
// DO NOT hardcode real resource IDs here.
const CONFIG = {
    // Cognito Configuration
    COGNITO: {
        REGION: window.__ENV__?.COGNITO_REGION || 'us-west-2',
        USER_POOL_ID: window.__ENV__?.COGNITO_USER_POOL_ID || '',
        CLIENT_ID: window.__ENV__?.COGNITO_CLIENT_ID || '',
        // Cognito domain (if using Hosted UI, otherwise we use AWS SDK directly)
        DOMAIN: null
    },

    // API Configuration
    API: {
        // When running through API Gateway
        GATEWAY_ENDPOINT: window.__ENV__?.API_GATEWAY_ENDPOINT || '',

        // When accessing service directly (for development)
        // Will auto-detect based on current location
        BASE_URL: window.location.origin + (window.location.pathname.startsWith('/prod') ? '/prod' : ''),

        // Use API Gateway or direct access
        USE_GATEWAY: false // Set to true if accessing via API Gateway
    },

    // Polling interval for status updates (ms)
    POLL_INTERVAL: 5000,

    // Auto-refresh interval for instance list (ms)
    REFRESH_INTERVAL: 30000
};

// Auto-detect if we're running through API Gateway
if (window.location.hostname.includes('execute-api')) {
    CONFIG.API.USE_GATEWAY = true;
}
