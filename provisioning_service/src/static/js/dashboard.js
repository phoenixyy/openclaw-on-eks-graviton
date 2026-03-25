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
    currentInstances: [],
    refreshTimer: null,
    isDeleting: false,
    isAdmin: false,
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

                // Check admin status (non-blocking)
                this.checkAdmin();
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

        // Admin buttons
        document.getElementById('admin-btn').addEventListener('click', () => this.showAdminPanel());
        document.getElementById('admin-refresh-btn').addEventListener('click', () => this.loadClusterData());
        document.getElementById('admin-back-btn').addEventListener('click', () => this.hideAdminPanel());

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
        // Allow creating multiple instances (remove the single-instance restriction)
        // if (this.currentInstances.length > 0) {
        //     this.showError('You already have an instance. Please delete it first.');
        //     return;
        // }
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
        createBtn.innerHTML = 'Create Agent';

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
            const instances = await API.getInstances();
            this.currentInstances = instances;

            if (instances && instances.length > 0) {
                this.showInstances(instances);
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
                this.showError(`Failed to load agents: ${msg}`);
            }
            this.showEmptyState();
        } finally {
            this.showLoading(false);
        }
    },

    // Show empty state (no instances)
    showEmptyState() {
        document.getElementById('empty-state').classList.remove('hidden');
        document.getElementById('instances-grid').classList.add('hidden');
        document.getElementById('create-instance-btn').disabled = false;
    },

    // Show instances grid
    showInstances(instances) {
        document.getElementById('empty-state').classList.add('hidden');
        document.getElementById('instances-grid').classList.remove('hidden');

        // Clear existing cards
        const grid = document.getElementById('instances-grid');
        grid.innerHTML = '';

        // Render each instance card
        instances.forEach(instance => {
            const card = this.renderInstanceCard(instance);
            grid.appendChild(card);
        });

        // Enable create button (multi-instance support)
        document.getElementById('create-instance-btn').disabled = false;
    },

    // Render a single instance card
    renderInstanceCard(instance) {
        const card = document.createElement('div');
        card.className = 'instance-card';
        card.dataset.instanceId = instance.instance_id;

        // Determine status class and badge text
        const statusClass = `status-${(instance.status || 'pending').toLowerCase()}`;
        const statusText = instance.status || 'Pending';

        // Format created date
        const createdDate = instance.created_at ? new Date(instance.created_at).toLocaleDateString() : 'Unknown';

        // Provider display
        const providerIcon = instance.provider === 'siliconflow' ? '🤖' : '☁️';
        const providerName = instance.provider === 'siliconflow' ? 'SiliconFlow' : 'Bedrock';

        // Isolation badges
        const isolation = instance.isolation || {};
        const storageIsolation = isolation.storage || {};
        const networkIsolation = isolation.network || {};
        const storageBadgeClass = storageIsolation.enabled ? 'storage-ok' : 'not-configured';
        const storageBadgeText = storageIsolation.enabled ? '🔒 Storage Isolated' : '🔒 Not Configured';
        const networkBadgeClass = networkIsolation.enabled ? 'network-ok' : 'not-configured';
        const networkBadgeText = networkIsolation.enabled ? '🛡️ Network Isolated' : '🛡️ Not Configured';
        const isolationBadgesHtml = `
            <div class="isolation-badges">
                <span class="isolation-badge ${storageBadgeClass}">${storageBadgeText}</span>
                <span class="isolation-badge ${networkBadgeClass}">${networkBadgeText}</span>
            </div>`;

        // Format full ISO created date
        const createdDateISO = instance.created_at ? new Date(instance.created_at).toISOString() : 'Unknown';

        // Gateway URL
        const gatewayUrl = instance.cloudfront_http_url || 'Not available yet';
        const gatewayUrlHtml = instance.cloudfront_http_url
            ? `<a href="${this.escapeHtml(gatewayUrl)}" target="_blank">${this.escapeHtml(gatewayUrl)}</a>`
            : gatewayUrl;

        // Ready status
        const readyStatus = instance.ready_for_connect ? 'Ready' : 'Not Ready';
        const readyStatusClass = instance.ready_for_connect ? 'status-running' : 'status-pending';

        // Build card HTML
        card.innerHTML = `
            <div class="instance-card-header">
                <h3 class="instance-card-title">${this.escapeHtml(instance.display_name || instance.instance_id)}</h3>
                <span class="status-badge ${statusClass}">${statusText}</span>
            </div>
            <div class="instance-card-body" data-expandable>
                <div class="instance-info-row">
                    <span class="instance-info-label">Agent ID</span>
                    <span class="instance-info-value">${this.escapeHtml(instance.instance_id)}</span>
                </div>
                <div class="instance-info-row">
                    <span class="instance-info-label">Provider</span>
                    <span class="instance-info-value">${providerIcon} ${providerName}</span>
                </div>
                <div class="instance-info-row">
                    <span class="instance-info-label">Model</span>
                    <span class="instance-info-value">${this.escapeHtml(instance.model || 'Unknown')}</span>
                </div>
                <div class="instance-info-row">
                    <span class="instance-info-label">Created</span>
                    <span class="instance-info-value">${createdDate}</span>
                </div>
                ${isolationBadgesHtml}
                <div class="expand-indicator"></div>
            </div>
            <div class="instance-details-section">
                <div class="instance-details-content">
                    <div class="detail-row">
                        <span class="detail-row-label">Agent ID</span>
                        <span class="detail-row-value"><code>${this.escapeHtml(instance.instance_id)}</code></span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-row-label">Display Name</span>
                        <span class="detail-row-value">${this.escapeHtml(instance.display_name || 'N/A')}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-row-label">Status</span>
                        <span class="detail-row-value"><span class="status-badge ${statusClass}">${statusText}</span></span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-row-label">Provider</span>
                        <span class="detail-row-value">${providerIcon} ${providerName}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-row-label">Model</span>
                        <span class="detail-row-value">${this.escapeHtml(instance.model || 'Unknown')}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-row-label">Created At (ISO)</span>
                        <span class="detail-row-value"><code>${this.escapeHtml(createdDateISO)}</code></span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-row-label">Gateway URL</span>
                        <span class="detail-row-value">${gatewayUrlHtml}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-row-label">Namespace</span>
                        <span class="detail-row-value"><code>${this.escapeHtml(instance.namespace || 'N/A')}</code></span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-row-label">Ready Status</span>
                        <span class="detail-row-value"><span class="status-badge ${readyStatusClass}">${readyStatus}</span></span>
                    </div>
                </div>
            </div>
            <div class="instance-card-actions">
                <button class="btn btn-success ${instance.ready_for_connect ? '' : 'disabled'}"
                        data-action="connect"
                        ${!instance.ready_for_connect ? 'disabled' : ''}>
                    <span>🔗</span> Connect
                </button>
                <button class="btn btn-danger" data-action="delete">
                    <span>🗑️</span> Delete
                </button>
            </div>
        `;

        // Add event listeners to buttons
        const connectBtn = card.querySelector('[data-action="connect"]');
        const deleteBtn = card.querySelector('[data-action="delete"]');

        connectBtn.addEventListener('click', (e) => {
            e.stopPropagation(); // Prevent expand/collapse
            this.handleConnectInstance(instance);
        });

        deleteBtn.addEventListener('click', (e) => {
            e.stopPropagation(); // Prevent expand/collapse
            this.handleDeleteInstance(instance);
        });

        // Add expand/collapse functionality
        const cardBody = card.querySelector('[data-expandable]');
        const detailsSection = card.querySelector('.instance-details-section');

        if (cardBody && detailsSection) {
            cardBody.addEventListener('click', () => {
                // Toggle expanded state
                const isExpanded = card.classList.contains('expanded');

                if (isExpanded) {
                    // Collapse
                    card.classList.remove('expanded');
                    detailsSection.classList.remove('expanded');
                } else {
                    // Expand
                    card.classList.add('expanded');
                    detailsSection.classList.add('expanded');
                }
            });
        }

        return card;
    },

    // Legacy single-instance display (kept for backward compatibility)
    showInstance(instance) {
        // Wrap single instance in array and call showInstances
        this.showInstances([instance]);
    },


    // Handle create instance (called from modal Create button)
    async handleCreateInstance() {
        const selectedProvider = this.modalSelectedProvider;
        const selectedModel = this.modalSelectedModel;
        const selectedRuntime = this.modalSelectedRuntime;

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
            const result = await API.createInstance(selectedRuntime, selectedProvider, siliconflowApiKey, selectedModel);
            console.log('Instance created:', result);

            // Close modal and show success
            this.closeCreateModal();
            this.showSuccess('Agent created! Waiting for it to appear...');

            // Show provisioning placeholder card
            const previousCount = this.currentInstances.length;
            this.addProvisioningPlaceholder(selectedProvider, selectedModel);

            // Poll /instances until new instance appears (max 30s, every 3s)
            const maxAttempts = 10;
            let attempt = 0;
            const pollInterval = setInterval(async () => {
                attempt++;
                try {
                    const instances = await API.getInstances();
                    if (instances && instances.length > previousCount) {
                        // New instance appeared — stop polling and refresh
                        clearInterval(pollInterval);
                        this.removeProvisioningPlaceholder();
                        this.currentInstances = instances;
                        this.showInstances(instances);
                        this.showSuccess('Agent provisioned successfully!');
                        return;
                    }
                } catch (e) {
                    console.warn('Poll attempt failed:', e);
                }
                if (attempt >= maxAttempts) {
                    clearInterval(pollInterval);
                    this.removeProvisioningPlaceholder();
                    // Final refresh — instance may have appeared on last poll
                    this.loadInstances();
                    this.showSuccess('Agent created. It may take a moment to appear.');
                }
            }, 3000);
        } catch (error) {
            console.error('Failed to create instance:', error);
            this.showError(`Failed to create agent: ${error.message}`);
            modalCreateBtn.disabled = false;
            modalCreateBtn.innerHTML = 'Create Agent';
        }
    },

    // Add a provisioning placeholder card to the grid
    addProvisioningPlaceholder(provider, model) {
        const grid = document.getElementById('instances-grid');
        const emptyState = document.getElementById('empty-state');

        // Ensure grid is visible
        emptyState.classList.add('hidden');
        grid.classList.remove('hidden');

        const placeholder = document.createElement('div');
        placeholder.className = 'instance-card provisioning-placeholder';
        placeholder.id = 'provisioning-placeholder';

        const providerIcon = provider === 'siliconflow' ? '🤖' : '☁️';
        const providerName = provider === 'siliconflow' ? 'SiliconFlow' : 'Bedrock';

        placeholder.innerHTML = `
            <div class="instance-card-header">
                <h3 class="instance-card-title">New Agent</h3>
                <span class="status-badge status-pending"><span class="provisioning-spinner"></span>Provisioning...</span>
            </div>
            <div class="instance-card-body">
                <div class="instance-info-row">
                    <span class="instance-info-label">Provider</span>
                    <span class="instance-info-value">${providerIcon} ${providerName}</span>
                </div>
                <div class="instance-info-row">
                    <span class="instance-info-label">Model</span>
                    <span class="instance-info-value">${this.escapeHtml(model || 'Default')}</span>
                </div>
                <div class="instance-info-row">
                    <span class="instance-info-label">Status</span>
                    <span class="instance-info-value">Setting up namespace, network policies, and instance...</span>
                </div>
            </div>
            <div class="instance-card-actions">
                <button class="btn btn-success disabled" disabled><span>🔗</span> Connect</button>
                <button class="btn btn-danger disabled" disabled><span>🗑️</span> Delete</button>
            </div>
        `;
        grid.appendChild(placeholder);
    },

    // Remove the provisioning placeholder card
    removeProvisioningPlaceholder() {
        const placeholder = document.getElementById('provisioning-placeholder');
        if (placeholder) placeholder.remove();
    },

    // Handle delete instance
    async handleDeleteInstance(instance) {
        if (!instance) {
            return;
        }

        const confirmation = prompt(
            `⚠️ WARNING: This will permanently delete agent "${instance.display_name || instance.instance_id}".\n\n` +
            `Type "DELETE" to confirm:`
        );

        if (confirmation !== 'DELETE') {
            return;
        }

        this.hideError();

        // Find the card and update its UI
        const card = document.querySelector(`[data-instance-id="${instance.instance_id}"]`);
        if (card) {
            const deleteBtn = card.querySelector('[data-action="delete"]');
            const connectBtn = card.querySelector('[data-action="connect"]');
            if (deleteBtn) deleteBtn.disabled = true;
            if (connectBtn) connectBtn.disabled = true;

            const statusBadge = card.querySelector('.status-badge');
            if (statusBadge) {
                statusBadge.textContent = 'Deleting...';
                statusBadge.className = 'status-badge status-pending';
            }
        }

        try {
            await API.deleteInstance(instance.instance_id);
            console.log('Instance deleted:', instance.instance_id);

            this.showSuccess('Agent deleted successfully!');

            // Refresh instances list after a delay
            setTimeout(() => this.loadInstances(), 2000);
        } catch (error) {
            console.error('Failed to delete instance:', error);
            this.showError(`Failed to delete agent: ${error.message}`);

            // Restore card state
            if (card) {
                const deleteBtn = card.querySelector('[data-action="delete"]');
                const connectBtn = card.querySelector('[data-action="connect"]');
                if (deleteBtn) deleteBtn.disabled = false;
                if (connectBtn && instance.ready_for_connect) connectBtn.disabled = false;
            }
        }
    },

    // Handle connect to instance - open gateway in new tab
    async handleConnectInstance(instance) {
        if (!instance) {
            this.showError('No agent selected');
            return;
        }

        // Use CloudFront HTTP URL (for browser access)
        const gatewayUrl = instance.cloudfront_http_url;

        if (!gatewayUrl) {
            this.showError('Gateway URL not available yet. Please wait for agent to be ready.');
            return;
        }

        try {
            // Clear Control UI localStorage to prevent token collision between instances.
            // Control UI stores gateway token in localStorage with fallback key
            // "openclaw.control.settings.v1" which is shared across all instances,
            // causing "token mismatch" when switching between different agents.
            try {
                localStorage.removeItem('openclaw.control.settings.v1');
                localStorage.removeItem('openclaw.control.token.v1');
                localStorage.removeItem('openclaw-device-identity-v1');
                console.log('🧹 Cleared Control UI localStorage for clean connect');
            } catch (e) {
                console.warn('Could not clear localStorage:', e);
            }

            // Open gateway in new tab
            window.open(gatewayUrl, '_blank');
            this.showSuccess(`Opening ${instance.display_name || instance.instance_id} in new tab...`);
        } catch (error) {
            console.error('Failed to open gateway:', error);
            this.showError(`Failed to open gateway: ${error.message}`);
        }
    },

    // Handle disconnect from instance
    handleDisconnectInstance() {
        this.wsManager.disconnect();

        // Hide WebSocket controls panel
        const wsControls = document.getElementById('ws-controls');
        if (wsControls) {
            wsControls.classList.add('hidden');
        }

        // Hide pairing notification if visible
        const pairingNotif = document.getElementById('pairing-notification');
        if (pairingNotif) {
            pairingNotif.classList.add('hidden');
        }

        this.showSuccess('Disconnected from gateway');
    },

    // Handle approve device (triggered by WebSocket pairing notification)
    async handleApproveDevice() {
        const notification = document.getElementById('pairing-notification');
        const requestId = notification?.dataset.requestId;

        if (!requestId || !this.currentInstance) {
            this.showError('No pending device pairing request');
            return;
        }

        try {
            this.hideError();
            const result = await API.approveDevice(
                this.currentInstance.user_id,
                requestId
            );

            if (result.success) {
                this.showSuccess('✅ Device approved successfully!');
                notification.classList.add('hidden');

                // Reconnect WebSocket after approval
                setTimeout(() => {
                    if (this.currentInstance && this.currentInstance.cloudfront_url) {
                        console.log('🔄 Reconnecting WebSocket after device approval...');
                        this.wsManager.connect(this.currentInstance.cloudfront_url);
                    }
                }, 1000);
            } else {
                this.showError('Failed to approve device');
            }
        } catch (error) {
            console.error('Failed to approve device:', error);
            this.showError(`Failed to approve device: ${error.message}`);
        }
    },

    // Handle approve device manually (auto-find pending request)
    async handleApproveDeviceManual() {
        if (!this.currentInstance) {
            this.showError('No agent selected');
            return;
        }

        const approveBtn = document.getElementById('approve-device-btn');
        const statusContainer = document.getElementById('device-approval-status');
        const statusMessage = document.getElementById('approval-status-message');

        try {
            this.hideError();

            // Set button to "Approving..." state
            approveBtn.disabled = true;
            approveBtn.innerHTML = '<span>⏳</span> Approving...';

            // Hide previous status
            if (statusContainer) {
                statusContainer.style.display = 'none';
            }

            // Call API with request_id=null, backend will auto-find pending request
            const result = await API.approveDevice(
                this.currentInstance.user_id,
                null  // request_id null triggers auto-find in backend
            );

            if (result.success) {
                // Success: Show approved button
                approveBtn.innerHTML = '<span>✓</span> Approved';
                approveBtn.classList.remove('btn-primary');
                approveBtn.classList.add('btn-success');
                // Keep button disabled - approved state

                // Show success message below gateway endpoint
                if (statusContainer && statusMessage) {
                    statusMessage.className = 'approval-status-message success';
                    statusMessage.innerHTML = '✅ Device approved successfully! You can now pair your devices.';
                    statusContainer.style.display = 'block';
                }

                // Reconnect WebSocket after approval
                if (this.wsManager.isConnected && this.currentInstance.cloudfront_url) {
                    setTimeout(() => {
                        console.log('🔄 Reconnecting WebSocket after device approval...');
                        this.wsManager.connect(this.currentInstance.cloudfront_url);
                    }, 1000);
                }
            } else {
                // Backend returned success=false, meaning no pending requests
                // Restore button to original state
                approveBtn.disabled = false;
                approveBtn.innerHTML = '<span>🔐</span> Approve Device';

                // Show warning message
                if (statusContainer && statusMessage) {
                    statusMessage.className = 'approval-status-message warning';
                    statusMessage.innerHTML = '⚠️ ' + (result.message || 'No pending device requests found');
                    statusContainer.style.display = 'block';
                }
            }
        } catch (error) {
            console.error('Failed to approve device:', error);

            // Restore button to original state
            approveBtn.disabled = false;
            approveBtn.innerHTML = '<span>❌</span> Approve Device';

            // Show error message
            if (statusContainer && statusMessage) {
                statusMessage.className = 'approval-status-message error';
                statusMessage.innerHTML = '❌ Failed to approve device: ' + error.message + ' (Click to retry)';
                statusContainer.style.display = 'block';
            }

            this.showError(`Failed to approve device: ${error.message}`);
        }
    },

    // Copy gateway endpoint to clipboard
    copyGatewayEndpoint() {
        const gatewayEl = document.getElementById('instance-gateway');
        const displayedText = gatewayEl ? gatewayEl.textContent : '';
        if (!displayedText || displayedText === 'Not available yet') {
            return;
        }

        navigator.clipboard.writeText(displayedText).then(() => {
            const copyBtn = document.getElementById('copy-gateway-btn');
            const originalText = copyBtn.textContent;
            copyBtn.textContent = '✓ Copied!';
            setTimeout(() => {
                copyBtn.textContent = originalText;
            }, 2000);
        }).catch(err => {
            console.error('Failed to copy:', err);
            this.showError('Failed to copy to clipboard');
        });
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
            // Only auto-refresh if instances exist
            if (this.currentInstances && this.currentInstances.length > 0) {
                this.loadInstances();
            }
        }, CONFIG.REFRESH_INTERVAL);
    },

    // ── Admin Panel Methods ──

    async checkAdmin() {
        try {
            const resp = await fetch(API.getBaseURL() + '/admin/cluster', { credentials: 'same-origin' });
            if (resp.ok) {
                this.isAdmin = true;
                const adminBtn = document.getElementById('admin-btn');
                if (adminBtn) adminBtn.classList.remove('hidden');
            }
        } catch (e) {
            // Not admin or endpoint unavailable — silently ignore
        }
    },

    showAdminPanel() {
        document.getElementById('instances-container').classList.add('hidden');
        document.querySelector('.actions-bar').classList.add('hidden');
        document.getElementById('admin-panel').classList.remove('hidden');
        this.loadClusterData();
    },

    hideAdminPanel() {
        document.getElementById('admin-panel').classList.add('hidden');
        document.getElementById('instances-container').classList.remove('hidden');
        document.querySelector('.actions-bar').classList.remove('hidden');
    },

    async loadClusterData() {
        try {
            const data = await API.request('/admin/cluster');
            this.renderClusterOverview(data);
        } catch (e) {
            console.error('Failed to load cluster data:', e);
        }
    },

    renderClusterOverview(data) {
        // Update summary cards
        document.getElementById('admin-instances-total').textContent = data.instances.total;
        document.getElementById('admin-instances-detail').textContent =
            `${data.instances.running} running / ${data.instances.pending} pending`;

        document.getElementById('admin-nodes-total').textContent = data.nodes.total;
        document.getElementById('admin-nodes-detail').textContent =
            `${data.nodes.system} system / ${data.nodes.karpenter_managed} karpenter`;

        document.getElementById('admin-pods-total').textContent = data.pods.total;
        document.getElementById('admin-pods-detail').textContent =
            `${data.pods.openclaw_pods} OpenClaw pods`;

        // Render nodes table
        const tbody = document.querySelector('#admin-nodes-table tbody');
        tbody.innerHTML = '';
        (data.nodes.details || []).forEach(node => {
            const managedClass = node.managed_by === 'karpenter' ? 'karpenter-node' : '';
            const row = document.createElement('tr');
            row.className = managedClass;
            row.innerHTML = `
                <td><code>${this.escapeHtml(node.name)}</code></td>
                <td>${this.escapeHtml(node.instance_type)}</td>
                <td>${this.escapeHtml(node.capacity.cpu)}</td>
                <td>${this.escapeHtml(node.capacity.memory)}</td>
                <td>${node.pods_count}</td>
                <td class="${node.cpu_percent != null && node.cpu_percent > 80 ? 'high-load' : ''}">${node.cpu_percent != null ? node.cpu_percent + '%' : '-'}</td>
                <td class="${node.memory_percent != null && node.memory_percent > 80 ? 'high-load' : ''}">${node.memory_percent != null ? node.memory_percent + '%' : '-'}</td>
                <td><span class="node-manager-badge ${node.managed_by}">${node.managed_by}</span></td>
                <td><span class="status-badge status-${node.status.toLowerCase()}">${node.status}</span></td>
                <td>${this.escapeHtml(node.age)}</td>
            `;
            tbody.appendChild(row);
        });

        // Render Karpenter events
        const eventsContainer = document.getElementById('admin-karpenter-events');
        const events = data.karpenter?.recent_events || [];
        if (events.length === 0) {
            eventsContainer.innerHTML = '<p class="text-muted">No recent Karpenter events</p>';
        } else {
            eventsContainer.innerHTML = events.map(e => `
                <div class="karpenter-event">
                    <span class="event-time">${new Date(e.timestamp).toLocaleTimeString()}</span>
                    <span class="event-reason">${this.escapeHtml(e.reason)}</span>
                    <span class="event-message">${this.escapeHtml(e.message)}</span>
                </div>
            `).join('');
        }
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
