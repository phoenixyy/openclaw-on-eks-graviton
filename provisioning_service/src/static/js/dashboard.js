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

        // Build card HTML
        card.innerHTML = `
            <div class="instance-card-header">
                <h3 class="instance-card-title">${this.escapeHtml(instance.display_name || instance.instance_id)}</h3>
                <span class="status-badge ${statusClass}">${statusText}</span>
            </div>
            <div class="instance-card-body">
                <div class="instance-info-row">
                    <span class="instance-info-label">Instance ID</span>
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

        connectBtn.addEventListener('click', () => {
            this.handleConnectInstance(instance);
        });

        deleteBtn.addEventListener('click', () => {
            this.handleDeleteInstance(instance);
        });

        return card;
    },

    // Legacy single-instance display (kept for backward compatibility)
    showInstance(instance) {
        // Wrap single instance in array and call showInstances
        this.showInstances([instance]);
    },


    // Handle create instance (called from modal Create button)
    async handleCreateInstance() {
        // Multi-instance support - no longer check for existing instance
        // if (this.currentInstances.length > 0) {
        //     this.showError('You already have an instance. Please delete it first.');
        //     this.closeCreateModal();
        //     return;
        // }

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

    // Handle delete instance
    async handleDeleteInstance(instance) {
        if (!instance) {
            return;
        }

        const confirmation = prompt(
            `⚠️ WARNING: This will permanently delete instance "${instance.display_name || instance.instance_id}".\n\n` +
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

            this.showSuccess('Instance deleted successfully!');

            // Refresh instances list after a delay
            setTimeout(() => this.loadInstances(), 2000);
        } catch (error) {
            console.error('Failed to delete instance:', error);
            this.showError(`Failed to delete instance: ${error.message}`);

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
            this.showError('No instance selected');
            return;
        }

        // Use CloudFront HTTP URL (for browser access)
        const gatewayUrl = instance.cloudfront_http_url;

        if (!gatewayUrl) {
            this.showError('Gateway URL not available yet. Please wait for instance to be ready.');
            return;
        }

        try {
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
            this.showError('No instance selected');
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
