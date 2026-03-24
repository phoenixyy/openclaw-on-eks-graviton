// WebSocket Manager for OpenClaw Gateway connections
class WebSocketManager {
    constructor() {
        this.ws = null;
        this.isConnected = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 3;
        this.messageHandlers = [];
        this.reconnectUrl = null;
    }

    connect(url) {
        if (this.ws) {
            this.disconnect();
        }

        console.log('🔌 Connecting to WebSocket:', url);
        this.reconnectUrl = url;
        this.ws = new WebSocket(url);

        this.ws.onopen = () => {
            console.log('✅ WebSocket connected');
            this.isConnected = true;
            this.reconnectAttempts = 0;
            this.updateStatus('connected');
        };

        this.ws.onmessage = (event) => {
            console.log('📨 WebSocket message:', event.data);
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (e) {
                console.error('❌ Failed to parse WebSocket message:', e);
            }
        };

        this.ws.onerror = (error) => {
            console.error('❌ WebSocket error:', error);
            this.updateStatus('error');
        };

        this.ws.onclose = (event) => {
            console.log('🔌 WebSocket closed:', event.code, event.reason);
            this.isConnected = false;
            this.updateStatus('disconnected');

            // Auto-reconnect with exponential backoff
            if (this.reconnectAttempts < this.maxReconnectAttempts && this.reconnectUrl) {
                this.reconnectAttempts++;
                const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts - 1), 5000);
                console.log(`🔄 Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})...`);
                setTimeout(() => {
                    if (this.reconnectUrl) {
                        this.connect(this.reconnectUrl);
                    }
                }, delay);
            }
        };
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.isConnected = false;
        this.reconnectUrl = null;
        this.reconnectAttempts = 0;
        this.updateStatus('disconnected');
    }

    send(data) {
        if (this.ws && this.isConnected) {
            this.ws.send(JSON.stringify(data));
        } else {
            console.error('❌ WebSocket not connected');
        }
    }

    handleMessage(data) {
        // Check for device pairing requests
        if (data.type === 'pairing_required' || data.type === 'device_pairing_required') {
            this.showPairingNotification(data);
            // Auto-approve: extract user_id from WS URL and call approve API
            this._autoApproveDevice(data.requestId || data.request_id);
        }

        // Call all registered message handlers
        this.messageHandlers.forEach(handler => handler(data));
    }

    _autoApproveDevice(requestId) {
        // Extract user_id from WebSocket URL: /instance/USER_ID?token=...
        let userId = null;
        if (this.reconnectUrl) {
            const match = this.reconnectUrl.match(/\/instance\/([^/?]+)/);
            if (match) userId = match[1];
        }
        if (!userId) {
            console.log('⚠️ Auto-approve: could not extract user_id from URL');
            return;
        }
        console.log('🤖 Auto-approving device pairing for user:', userId, 'requestId:', requestId);
        const body = { user_id: userId };
        if (requestId) body.request_id = requestId;
        
        // Get the base URL for the API call (same origin as the provisioning dashboard)
        const apiBase = window.location.origin + (window.location.pathname.startsWith('/prod') ? '/prod' : '');
        fetch(apiBase + '/api/devices/approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body)
        }).then(r => r.json()).then(result => {
            if (result && result.success) {
                console.log('✅ Device auto-approved, reconnecting...');
                const container = document.getElementById('pairing-notification');
                if (container) container.classList.add('hidden');
                // Reconnect after a short delay
                setTimeout(() => {
                    if (this.reconnectUrl) this.connect(this.reconnectUrl);
                }, 1500);
            } else {
                console.log('ℹ️ Auto-approve result:', result);
            }
        }).catch(err => console.log('ℹ️ Auto-approve error:', err));
    }

    showPairingNotification(data) {
        const container = document.getElementById('pairing-notification');
        if (container) {
            container.classList.remove('hidden');
            container.dataset.requestId = data.requestId || data.request_id || '';
        }
    }

    updateStatus(status) {
        const statusEl = document.getElementById('ws-status');
        if (!statusEl) return;

        const statusConfig = {
            'connected': { text: '🟢 Connected', class: 'status-connected' },
            'disconnected': { text: '🔴 Disconnected', class: 'status-disconnected' },
            'error': { text: '🟡 Error', class: 'status-error' }
        };

        const config = statusConfig[status] || statusConfig.disconnected;
        statusEl.textContent = config.text;
        statusEl.className = `ws-status ${config.class}`;
    }

    onMessage(handler) {
        this.messageHandlers.push(handler);
    }
}

// Dashboard page logic
const Dashboard = {
    instances: [],
    refreshTimer: null,
    wsManager: new WebSocketManager(),

    // Initialize dashboard
    init() {
        console.log('🚀 Dashboard initializing...');

        // Check authentication via session
        fetch('/me', {
            credentials: 'same-origin'  // Include cookies (session)
        })
            .then(response => {
                if (!response.ok) {
                    throw new Error('Not authenticated');
                }
                return response.json();
            })
            .then(data => {
                // Store user session
                Auth.session = data.user;

                // Display user email
                document.getElementById('user-email').textContent = data.user.email;

                // Setup event listeners
                this.setupEventListeners();

                // Load models list
                this.loadModels();

                // Load user's instances
                this.loadInstances();

                // Setup auto-refresh
                this.startAutoRefresh();
            })
            .catch(error => {
                console.error('❌ Authentication check failed:', error);
                this.showError('Not authenticated. Redirecting to login...');
                this.showEmptyState();
                const loginPath = window.location.pathname.startsWith('/prod') ? '/prod/login' : '/login';
                setTimeout(() => window.location.href = loginPath, 2000);
            });
    },
    // Modal state
    modalSelectedProvider: 'bedrock',
    modalSelectedModel: null,
    modalSelectedRuntime: 'runc',

    // Setup event listeners
    setupEventListeners() {
        // Logout button
        document.getElementById('logout-btn').addEventListener('click', () => {
            this.handleLogout();
        });

        // Create instance button → open modal
        document.getElementById('create-instance-btn').addEventListener('click', () => {
            this.openCreateModal();
        });

        // Empty-state CTA also opens modal
        const emptyStateBtn = document.getElementById('empty-state-create-btn');
        if (emptyStateBtn) {
            emptyStateBtn.addEventListener('click', () => this.openCreateModal());
        }

        // Refresh button
        document.getElementById('refresh-btn').addEventListener('click', () => {
            this.loadInstances();
        });

        // ── Modal wiring ──
        const modal = document.getElementById('create-modal');

        // Backdrop click → close
        modal.addEventListener('click', (e) => {
            if (e.target === modal) this.closeCreateModal();
        });

        // Escape key → close
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && modal.classList.contains('open')) this.closeCreateModal();
        });

        // Cancel button
        document.getElementById('modal-cancel-btn').addEventListener('click', () => {
            this.closeCreateModal();
        });

        // Create button
        document.getElementById('modal-create-btn').addEventListener('click', () => {
            this.handleCreateInstance();
        });

        // Provider toggle
        document.querySelectorAll('#modal-provider-group .modal-toggle-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('#modal-provider-group .modal-toggle-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                if (btn.dataset.provider === 'siliconflow') btn.classList.add('accent-orange');
                this.modalSelectedProvider = btn.dataset.provider;
                // Show/hide API key
                const row = document.getElementById('modal-apikey-row');
                if (btn.dataset.provider === 'siliconflow') {
                    row.classList.add('visible');
                } else {
                    row.classList.remove('visible');
                }
                // Re-render model cards
                this.populateModelCards(btn.dataset.provider);
            });
        });

        // Runtime toggle
        document.querySelectorAll('#modal-runtime-group .modal-toggle-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('#modal-runtime-group .modal-toggle-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                if (btn.dataset.runtime === 'kata-qemu') btn.classList.add('accent-orange');
                this.modalSelectedRuntime = btn.dataset.runtime;
            });
        });
    },

    // Open the create-instance modal
    async openCreateModal() {
        // Check if user has reached max instances
        if (this.instances.length >= CONFIG.MAX_INSTANCES) {
            this.showError(`Maximum instances reached (${CONFIG.MAX_INSTANCES} per user)`);
            return;
        }
        // Ensure models are loaded before opening
        if (!this.allModels.bedrock && !this.allModels.siliconflow) {
            await this.loadModels();
        }
        // Reset modal state
        this.modalSelectedProvider = 'bedrock';
        this.modalSelectedRuntime = 'runc';
        this.modalSelectedModel = null;

        // Reset provider toggle
        document.querySelectorAll('#modal-provider-group .modal-toggle-btn').forEach(b => {
            b.classList.remove('active', 'accent-orange');
        });
        document.querySelector('#modal-provider-group [data-provider="bedrock"]').classList.add('active');

        // Reset runtime toggle
        document.querySelectorAll('#modal-runtime-group .modal-toggle-btn').forEach(b => {
            b.classList.remove('active', 'accent-orange');
        });
        document.querySelector('#modal-runtime-group [data-runtime="runc"]').classList.add('active');

        // Hide API key row and clear value
        document.getElementById('modal-apikey-row').classList.remove('visible');
        document.getElementById('modal-apikey').value = '';

        // Reset create button
        const createBtn = document.getElementById('modal-create-btn');
        createBtn.disabled = false;
        createBtn.innerHTML = 'Create Instance';

        // Populate model cards
        this.populateModelCards('bedrock');

        // Show modal
        document.getElementById('create-modal').classList.add('open');
    },

    // Close the modal
    closeCreateModal() {
        document.getElementById('create-modal').classList.remove('open');
    },

    // All models keyed by provider
    allModels: {},

    // Load available models
    async loadModels() {
        try {
            const data = await API.getModels();
            if (!data.bedrock && !data.siliconflow) return;
            this.allModels = data;
        } catch (error) {
            console.error('Failed to load models:', error);
        }
    },

    // Render model cards for a given provider inside the modal
    populateModelCards(provider) {
        const list = document.getElementById('modal-model-list');
        if (!list) return;

        const models = this.allModels[provider] || [];
        if (models.length === 0) {
            list.innerHTML = '<div class="model-card-list-loading">Loading models...</div>';
            this.modalSelectedModel = null;
            return;
        }

        list.innerHTML = '';
        let defaultId = null;

        models.forEach(model => {
            const card = document.createElement('div');
            card.className = 'model-card';
            card.dataset.modelId = model.id;

            const isDefault = !!model.default;
            if (isDefault) defaultId = model.id;

            card.innerHTML =
                '<div class="model-card-radio"></div>' +
                '<div class="model-card-info">' +
                    '<div class="model-card-name">' + this.escapeHtml(model.name) + '</div>' +
                    '<div class="model-card-provider">' + this.escapeHtml(model.provider_label) + '</div>' +
                '</div>' +
                (isDefault ? '<span class="model-card-default">Default</span>' : '');

            card.addEventListener('click', () => {
                list.querySelectorAll('.model-card').forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
                this.modalSelectedModel = model.id;
            });

            list.appendChild(card);
        });

        // Pre-select default model
        if (defaultId) {
            const defaultCard = list.querySelector(`[data-model-id="${CSS.escape(defaultId)}"]`);
            if (defaultCard) {
                defaultCard.classList.add('selected');
                this.modalSelectedModel = defaultId;
            }
        } else if (models.length > 0) {
            list.firstChild.classList.add('selected');
            this.modalSelectedModel = models[0].id;
        }
    },

    // Escape HTML helper
    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },

    // Handle logout
    handleLogout() {
        this.stopAutoRefresh();
        Auth.logout();
        const loginPath = window.location.pathname.startsWith('/prod') ? '/prod/login' : '/login';
        window.location.href = loginPath;
    },

    // Load user's instances
    async loadInstances() {
        this.showLoading(true);
        this.hideError();

        try {
            const data = await API.getInstances();
            this.instances = data.instances || [];

            if (this.instances.length > 0) {
                this.showInstancesList(this.instances);
            } else {
                this.showEmptyState();
            }
        } catch (error) {
            console.error('Failed to load instances:', error);
            const msg = error.message || '';
            // Suppress "not found", auth errors — just show empty state
            const isExpected = msg.includes('404') || msg.includes('not found')
                || msg.includes('401') || msg.includes('403')
                || msg.includes('Not authenticated');
            if (!isExpected) {
                this.showError(`Failed to load instances: ${msg}`);
            }
            this.showEmptyState();
        } finally {
            this.showLoading(false);
        }
    },

    // Show empty state (no instances)
    showEmptyState() {
        document.getElementById('empty-state').classList.remove('hidden');
        document.getElementById('instances-list').classList.add('hidden');

        // Enable create button
        document.getElementById('create-instance-btn').disabled = false;
    },

    // Show instances list
    showInstancesList(instances) {
        document.getElementById('empty-state').classList.add('hidden');
        document.getElementById('instances-list').classList.remove('hidden');

        const container = document.getElementById('instances-container');
        container.innerHTML = '';

        instances.forEach(instance => {
            const card = this.createInstanceCard(instance);
            container.appendChild(card);
        });

        // Enable create button if under limit
        document.getElementById('create-instance-btn').disabled = instances.length >= CONFIG.MAX_INSTANCES;
    },

    // Create instance card HTML
    createInstanceCard(instance) {
        const card = document.createElement('div');
        card.className = 'instance-card';
        card.dataset.instanceId = instance.instance_id;

        const status = instance.status || 'Pending';
        const statusMessage = instance.status_message || status;
        const readyForConnect = instance.ready_for_connect === true;

        card.innerHTML = `
            <div class="instance-card-header">
                <div>
                    <h3 class="instance-card-title">${this.escapeHtml(instance.display_name || instance.instance_id)}</h3>
                    <div class="instance-card-meta">
                        <span class="instance-id">${this.escapeHtml(instance.instance_id)}</span>
                        <span class="instance-provider">${this.escapeHtml(instance.llm_provider || 'bedrock')}</span>
                    </div>
                </div>
                <span class="status-badge status-${status.toLowerCase()}">${this.escapeHtml(statusMessage)}</span>
            </div>
            <div class="instance-card-body">
                <div class="instance-detail">
                    <label>Created</label>
                    <span>${new Date(instance.created_at).toLocaleString()}</span>
                </div>
                ${instance.cloudfront_http_url ? `
                <div class="instance-detail">
                    <label>Gateway URL</label>
                    <div class="gateway-url">
                        <code>${this.escapeHtml(instance.cloudfront_http_url)}</code>
                        <button class="btn-copy" onclick="Dashboard.copyToClipboard('${this.escapeHtml(instance.cloudfront_http_url)}')">📋</button>
                    </div>
                </div>
                ` : '<div class="instance-detail"><label>Gateway URL</label><span>Generating...</span></div>'}
            </div>
            <div class="instance-card-actions">
                ${readyForConnect ? `
                    <button class="btn btn-success" onclick="Dashboard.connectInstance('${instance.instance_id}', '${this.escapeHtml(instance.cloudfront_http_url || '')}')">
                        🔗 Connect
                    </button>
                ` : `
                    <button class="btn btn-success" disabled>
                        ⏳ ${this.escapeHtml(statusMessage)}
                    </button>
                `}
                <button class="btn btn-danger" onclick="Dashboard.deleteInstanceConfirm('${instance.instance_id}')">
                    🗑️ Delete
                </button>
            </div>
        `;

        return card;
    },

    // Connect to instance
    connectInstance(instanceId, gatewayUrl) {
        if (!gatewayUrl) {
            this.showError('Gateway URL not available yet');
            return;
        }
        window.open(gatewayUrl, '_blank');
        this.showSuccess('Opening gateway in new tab...');
    },

    // Copy to clipboard helper
    copyToClipboard(text) {
        navigator.clipboard.writeText(text).then(() => {
            this.showSuccess('Copied to clipboard!');
        }).catch(err => {
            console.error('Failed to copy:', err);
            this.showError('Failed to copy to clipboard');
        });
    },

    // Handle create instance (called from modal Create button)
    async handleCreateInstance() {
        const selectedProvider = this.modalSelectedProvider;
        const selectedModel = this.modalSelectedModel;
        const selectedRuntime = this.modalSelectedRuntime;

        // Get display name (optional)
        const displayName = document.getElementById('modal-display-name')?.value?.trim() || '';

        // Validate SiliconFlow API key
        let siliconflowApiKey = null;
        if (selectedProvider === 'siliconflow') {
            siliconflowApiKey = document.getElementById('modal-apikey')?.value?.trim();
            if (!siliconflowApiKey) {
                this.showError('Please enter your SiliconFlow API key.');
                return;
            }
            if (!siliconflowApiKey.startsWith('sk-')) {
                this.showError('SiliconFlow API key should start with "sk-".');
                return;
            }
        }

        // Show spinner on modal create button
        const modalCreateBtn = document.getElementById('modal-create-btn');
        modalCreateBtn.disabled = true;
        modalCreateBtn.innerHTML = '<span class="modal-btn-spinner"></span> Creating...';
        this.hideError();

        try {
            const result = await API.createInstance(selectedRuntime, selectedProvider, siliconflowApiKey, selectedModel, displayName);
            console.log('Instance created:', result);

            // Close modal and show success
            this.closeCreateModal();
            this.showSuccess('Instance created successfully! Loading...');

            // Refresh after a delay
            setTimeout(() => this.loadInstances(), 2000);
        } catch (error) {
            console.error('Failed to create instance:', error);
            this.showError(`Failed to create instance: ${error.message}`);
            modalCreateBtn.disabled = false;
            modalCreateBtn.innerHTML = 'Create Instance';
        }
    },

    // Confirm and delete instance
    deleteInstanceConfirm(instanceId) {
        const confirmation = prompt(
            `⚠️ WARNING: This will permanently delete instance ${instanceId}.\n\n` +
            `Type "DELETE" to confirm:`
        );

        if (confirmation === 'DELETE') {
            this.deleteInstance(instanceId);
        }
    },

    // Handle delete instance
    async deleteInstance(instanceId) {
        this.hideError();

        // Find and update the card UI
        const card = document.querySelector(`[data-instance-id="${instanceId}"]`);
        if (card) {
            const statusBadge = card.querySelector('.status-badge');
            if (statusBadge) {
                statusBadge.textContent = 'Deleting...';
                statusBadge.className = 'status-badge status-warning';
            }
            const buttons = card.querySelectorAll('button');
            buttons.forEach(btn => btn.disabled = true);
        }

        try {
            await API.deleteInstance(instanceId);
            console.log('Instance deleted:', instanceId);

            this.showSuccess('Instance deleted successfully!');

            // Refresh instances list
            setTimeout(() => this.loadInstances(), 2000);
        } catch (error) {
            console.error('Failed to delete instance:', error);
            this.showError(`Failed to delete instance: ${error.message}`);

            // Re-enable buttons on error
            if (card) {
                const buttons = card.querySelectorAll('button');
                buttons.forEach(btn => btn.disabled = false);
            }
        }
    },


    // Show/hide loading
    showLoading(show) {
        const loadingEl = document.getElementById('loading');
        if (show) {
            loadingEl.classList.remove('hidden');
        } else {
            loadingEl.classList.add('hidden');
        }
    },

    // Show error message
    showError(message) {
        const errorEl = document.getElementById('error-message');
        errorEl.textContent = '❌ ' + message;
        errorEl.className = 'error-banner';
        errorEl.classList.remove('hidden');
    },

    // Show success message
    showSuccess(message) {
        const errorEl = document.getElementById('error-message');
        errorEl.textContent = '✅ ' + message;
        errorEl.className = 'error-banner success-banner';
        errorEl.classList.remove('hidden');

        // Auto-hide after 5 seconds
        setTimeout(() => this.hideError(), 5000);
    },

    // Hide error message
    hideError() {
        const errorEl = document.getElementById('error-message');
        errorEl.classList.add('hidden');
    },

    // Start auto-refresh
    startAutoRefresh() {
        this.stopAutoRefresh();
        this.refreshTimer = setInterval(() => {
            // Auto-refresh instances list
            if (this.instances.length > 0) {
                this.loadInstances();
            }
        }, CONFIG.REFRESH_INTERVAL);
    },

    // Stop auto-refresh
    stopAutoRefresh() {
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }
    }
};

// Initialize dashboard when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    Dashboard.init();
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    Dashboard.stopAutoRefresh();
});
