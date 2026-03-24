// Authentication utilities
const Auth = {
    session: null,

    // Logout
    logout() {
        fetch('/logout', {
            method: 'POST',
            credentials: 'same-origin'
        }).then(() => {
            window.location.href = '/login';
        }).catch(err => {
            console.error('Logout failed:', err);
            // Force redirect anyway
            window.location.href = '/login';
        });
    },

    // Check if user is authenticated
    async checkAuth() {
        try {
            const response = await fetch('/me', {
                credentials: 'same-origin'
            });
            if (response.ok) {
                const data = await response.json();
                this.session = data.user;
                return true;
            }
            return false;
        } catch (error) {
            console.error('Auth check failed:', error);
            return false;
        }
    }
};
