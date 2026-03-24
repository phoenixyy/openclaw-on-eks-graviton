// Session-based Authentication module
const Auth = {
    // Current user session
    session: null,

    // Initialize authentication
    init() {
        // Session is managed by Flask server-side cookies
        // No client-side initialization needed
        return true; // Always return true for session-based auth
    },

    // Get current user email (will be fetched from /me endpoint)
    getUserEmail() {
        return this.session ? this.session.email : null;
    },

    // Get authorization header (not needed for session-based auth)
    getAuthHeader() {
        // Session auth uses HTTP-only cookies automatically sent by browser
        // No Authorization header needed
        return {};
    },

    // Sign out
    logout() {
        // Clear client-side session
        this.session = null;
    }
};
