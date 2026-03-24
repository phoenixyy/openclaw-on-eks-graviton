// API Client for OpenClaw Provisioning Service
const API = {
    // Get current user info
    async getMe() {
        const response = await fetch('/me', {
            credentials: 'same-origin'
        });
        if (!response.ok) {
            throw new Error('Not authenticated');
        }
        return response.json();
    },

    // Get all instances for current user
    async getInstances() {
        const response = await fetch('/instances', {
            credentials: 'same-origin'
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch instances');
        }
        return response.json();
    },

    // Get single instance (backward compatibility)
    async getMyInstance() {
        const data = await this.getInstances();
        // Return first instance if exists, otherwise null
        return data.instances && data.instances.length > 0 ? data.instances[0] : null;
    },

    // Get instance by ID
    async getInstance(instanceId) {
        const response = await fetch(`/status/${instanceId}`, {
            credentials: 'same-origin'
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch instance');
        }
        return response.json();
    },

    // Create new instance
    async createInstance(runtimeClass, provider, siliconflowApiKey, model, displayName) {
        const body = {
            provider: provider || 'bedrock',
            model: model || null
        };

        // Add display_name if provided
        if (displayName) {
            body.display_name = displayName;
        }

        // Add runtime class to config
        if (runtimeClass && runtimeClass !== 'runc') {
            body.config = {
                runtime_class: runtimeClass
            };
        }

        // Add SiliconFlow API key if provider is siliconflow
        if (provider === 'siliconflow' && siliconflowApiKey) {
            body.siliconflow_api_key = siliconflowApiKey;
        }

        const response = await fetch('/provision', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'same-origin',
            body: JSON.stringify(body)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to create instance');
        }

        return response.json();
    },

    // Delete instance
    async deleteInstance(instanceId) {
        const response = await fetch(`/delete/${instanceId}`, {
            method: 'DELETE',
            credentials: 'same-origin'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to delete instance');
        }

        return response.json();
    },

    // Get available models
    async getModels() {
        const response = await fetch('/models', {
            credentials: 'same-origin'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch models');
        }

        return response.json();
    },

    // Approve device pairing
    async approveDevice(instanceId, requestId) {
        const body = {
            instance_id: instanceId
        };

        if (requestId) {
            body.request_id = requestId;
        }

        const response = await fetch('/api/devices/approve', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'same-origin',
            body: JSON.stringify(body)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to approve device');
        }

        return response.json();
    }
};
