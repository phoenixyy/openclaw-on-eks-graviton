/**
 * Billing Panel JavaScript
 * Handles quota display, usage charts, and plan management
 */

const Billing = {
    chart: null,

    /**
     * Initialize billing panel
     */
    init: function() {
        console.log('Initializing billing panel...');
        this.injectBillingPanel();
        this.loadBillingData();
    },

    /**
     * Inject billing panel HTML into dashboard
     */
    injectBillingPanel: function() {
        const instanceCard = document.querySelector('.instance-card-large');
        if (!instanceCard || !instanceCard.parentElement) {
            console.warn('Could not find instance card to insert billing panel');
            return;
        }

        const billingHTML = `
            <!-- Billing Panel Section -->
            <div class="billing-section" id="billing-section">
                <!-- Section Header -->
                <div class="billing-header">
                    <span class="billing-icon">📊</span>
                    <h2>Usage & Billing</h2>
                </div>

                <!-- Stats Cards -->
                <div class="billing-stats-grid">
                    <div class="billing-stat-card">
                        <div class="stat-label">
                            <span class="stat-label-icon">💬</span>
                            Total Tokens
                        </div>
                        <div class="stat-value tokens" id="billing-total-tokens">0</div>
                        <div class="stat-sublabel">Last 30 days</div>
                    </div>

                    <div class="billing-stat-card">
                        <div class="stat-label">
                            <span class="stat-label-icon">🔄</span>
                            API Calls
                        </div>
                        <div class="stat-value calls" id="billing-calls">0</div>
                        <div class="stat-sublabel">Last 30 days</div>
                    </div>

                    <div class="billing-stat-card quota-card">
                        <div class="stat-label">
                            <span class="stat-label-icon">⚠️</span>
                            Monthly Quota
                        </div>
                        <div class="quota-bar-container">
                            <div class="quota-bar">
                                <div class="quota-fill" id="quota-fill" style="width: 0%"></div>
                            </div>
                            <div class="quota-text">
                                <span id="quota-used">0</span> / <span id="quota-limit">100K</span> tokens
                            </div>
                        </div>
                        <div class="stat-sublabel">
                            <span id="quota-status">🟢 Within limit</span> ·
                            Resets in <span id="days-until-reset">--</span> days
                        </div>
                    </div>
                </div>

                <!-- Plan Info Banner -->
                <div class="plan-info-banner" id="plan-info-banner">
                    <div class="plan-badge" id="current-plan-badge">FREE PLAN</div>
                    <div class="plan-features">
                        <span>📦 1 Instance</span>
                        <span>💬 100K tokens/month</span>
                        <span>🆓 $0/month</span>
                    </div>
                    <button class="btn btn-primary btn-upgrade" id="upgrade-plan-btn">
                        Upgrade Plan
                    </button>
                </div>

                <!-- Model Breakdown Table -->
                <div class="billing-models-section">
                    <div class="models-header">
                        <div class="models-title">Model Breakdown</div>
                    </div>
                    <div class="models-table-container">
                        <table class="models-table">
                            <thead>
                                <tr>
                                    <th>Provider</th>
                                    <th>Model</th>
                                    <th>Tokens Used</th>
                                </tr>
                            </thead>
                            <tbody id="models-table-body">
                                <tr>
                                    <td colspan="3">
                                        <div class="empty-state">
                                            <div class="empty-state-icon">📈</div>
                                            <p class="empty-state-text">Loading usage data...</p>
                                        </div>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;

        instanceCard.parentElement.insertAdjacentHTML('beforeend', billingHTML);

        // Add event listener for upgrade button
        const upgradeBtn = document.getElementById('upgrade-plan-btn');
        if (upgradeBtn) {
            upgradeBtn.addEventListener('click', () => this.showUpgradeModal());
        }
    },

    /**
     * Load billing data from API
     *
     * NOTE: Billing is planned as a separate microservice.
     * For now, we show mock data (all zeros) to avoid 500 errors.
     * Future: Connect to billing microservice API.
     */
    loadBillingData: async function() {
        try {
            const response = await fetch('/billing/usage', {
                credentials: 'include'
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            console.log('Billing data loaded:', data);
            this.updateBillingUI(data);
        } catch (error) {
            console.error('Failed to load billing data:', error);
            this.showError('Failed to load billing data');
        }
    },

    /**
     * Update billing UI with data
     */
    updateBillingUI: function(data) {
        // Update tokens and calls
        document.getElementById('billing-total-tokens').textContent =
            data.summary.total_tokens.toLocaleString();
        document.getElementById('billing-calls').textContent =
            data.summary.total_calls.toLocaleString();

        // Update quota
        if (data.quota) {
            this.updateQuotaDisplay(data.quota, data.days_until_reset);
        }

        // Update plan info
        if (data.plan) {
            this.updatePlanInfo(data.plan);
        }

        // Update model breakdown
        if (data.by_model && data.by_model.length > 0) {
            this.renderModelBreakdown(data.by_model);
        } else {
            this.showEmptyModelBreakdown();
        }
    },

    /**
     * Update quota display
     */
    updateQuotaDisplay: function(quota, daysUntilReset) {
        const percentage = Math.min(quota.percentage_used, 100);
        const used = quota.current_usage;
        const limit = quota.limit;

        // Update fill bar
        const fillElement = document.getElementById('quota-fill');
        fillElement.style.width = `${percentage}%`;

        // Set color based on status
        if (quota.is_over_quota) {
            fillElement.style.background = 'linear-gradient(90deg, #ff5757, #ff8787)';
        } else if (quota.is_warning) {
            fillElement.style.background = 'linear-gradient(90deg, #ffd93d, #ffea85)';
        } else {
            fillElement.style.background = 'linear-gradient(90deg, #00ff9d, #00d4ff)';
        }

        // Update used/limit text
        const formatTokens = (tokens) => {
            if (tokens >= 1_000_000) {
                return (tokens / 1_000_000).toFixed(1) + 'M';
            } else if (tokens >= 1_000) {
                return (tokens / 1_000).toFixed(0) + 'K';
            } else {
                return tokens.toString();
            }
        };

        document.getElementById('quota-used').textContent = formatTokens(used);

        if (limit) {
            document.getElementById('quota-limit').textContent = formatTokens(limit);
        } else {
            document.getElementById('quota-limit').textContent = '∞';
        }

        // Update status text
        const statusElement = document.getElementById('quota-status');
        statusElement.textContent = `${quota.status_emoji} ${quota.status_text}`;

        let statusColor = '#00ff9d'; // success
        if (quota.is_over_quota) {
            statusColor = '#ff5757'; // danger
        } else if (quota.is_warning) {
            statusColor = '#ffd93d'; // warning
        }
        statusElement.style.color = statusColor;

        // Update reset countdown
        document.getElementById('days-until-reset').textContent = daysUntilReset || '--';
    },

    /**
     * Update plan info banner
     */
    updatePlanInfo: function(plan) {
        const planBadge = document.getElementById('current-plan-badge');
        const upgradeBtn = document.getElementById('upgrade-plan-btn');

        planBadge.textContent = plan.toUpperCase() + ' PLAN';

        // Update badge color
        planBadge.classList.remove('plan-free', 'plan-pro', 'plan-enterprise');
        planBadge.classList.add(`plan-${plan}`);

        // Hide upgrade button for enterprise
        if (plan === 'enterprise') {
            upgradeBtn.style.display = 'none';
        } else {
            upgradeBtn.style.display = 'block';
        }
    },

    /**
     * Render model breakdown table
     */
    renderModelBreakdown: function(models) {
        const tbody = document.getElementById('models-table-body');

        const rows = models.map(model => `
            <tr>
                <td>
                    <span class="provider-badge provider-${model.provider}">
                        ${model.provider.toUpperCase()}
                    </span>
                </td>
                <td>
                    <span class="model-name">${model.model}</span>
                </td>
                <td>
                    <span class="token-count">${model.total_tokens.toLocaleString()}</span>
                </td>
            </tr>
        `).join('');

        tbody.innerHTML = rows;
    },

    /**
     * Show empty model breakdown
     */
    showEmptyModelBreakdown: function() {
        const tbody = document.getElementById('models-table-body');
        tbody.innerHTML = `
            <tr>
                <td colspan="3">
                    <div class="empty-state">
                        <div class="empty-state-icon">📊</div>
                        <p class="empty-state-text">No usage data yet. Start using your instance!</p>
                    </div>
                </td>
            </tr>
        `;
    },

    /**
     * Show upgrade modal
     */
    showUpgradeModal: async function() {
        // Fetch available plans
        try {
            const response = await fetch('/billing/plans', {
                credentials: 'include'
            });

            if (!response.ok) {
                throw new Error('Failed to fetch plans');
            }

            const { plans } = await response.json();

            // Simple prompt for MVP (can be replaced with modal later)
            const planChoice = prompt(
                'Choose a plan:\n' +
                'free: $0/month, 100K tokens\n' +
                'pro: $99/month, 10M tokens\n' +
                'enterprise: Custom pricing, unlimited tokens\n\n' +
                'Enter plan name (free/pro/enterprise):'
            );

            if (!planChoice) return;

            if (!plans[planChoice]) {
                alert('Invalid plan choice');
                return;
            }

            // Confirm upgrade
            const confirmed = confirm(
                `Upgrade to ${planChoice.toUpperCase()} plan?\n\n` +
                `Price: $${plans[planChoice].price_monthly || 'Custom'}/month\n` +
                `Tokens: ${plans[planChoice].tokens_per_month ? plans[planChoice].tokens_per_month.toLocaleString() : 'Unlimited'}/month\n` +
                `Instances: ${plans[planChoice].max_instances || 'Unlimited'}`
            );

            if (!confirmed) return;

            // Perform upgrade
            await this.upgradePlan(planChoice);

        } catch (error) {
            console.error('Failed to show upgrade modal:', error);
            alert('Failed to load plans');
        }
    },

    /**
     * Upgrade user plan
     */
    upgradePlan: async function(newPlan) {
        try {
            const response = await fetch('/billing/upgrade', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                credentials: 'include',
                body: JSON.stringify({ plan: newPlan })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Upgrade failed');
            }

            const result = await response.json();
            console.log('Plan upgraded:', result);

            alert(`✅ Successfully upgraded to ${result.new_plan.toUpperCase()} plan!`);

            // Reload billing data
            this.loadBillingData();

        } catch (error) {
            console.error('Failed to upgrade plan:', error);
            alert(`Failed to upgrade plan: ${error.message}`);
        }
    },

    /**
     * Show error message
     */
    showError: function(message) {
        const tbody = document.getElementById('models-table-body');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="3">
                        <div class="empty-state">
                            <div class="empty-state-icon">❌</div>
                            <p class="empty-state-text">${message}</p>
                        </div>
                    </td>
                </tr>
            `;
        }
    }
};

// Auto-initialize when document is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => Billing.init());
} else {
    Billing.init();
}
