// Frontend configuration
const CONFIG = {
    // Auto-refresh interval (in milliseconds)
    REFRESH_INTERVAL: 10000,  // 10 seconds

    // API base path
    API_BASE: window.location.pathname.startsWith('/prod') ? '/prod' : '',

    // Max instances per user
    MAX_INSTANCES: 100
};
