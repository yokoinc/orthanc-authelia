/**
 * Token Manager JavaScript - OE2 Design
 * Interface for managing PACS sharing tokens
 */

const CONFIG = {
    REFRESH_INTERVAL: 30000,
    DEBUG_MODE: false,
    MESSAGES: {
        EXPIRED: "Expired",
        NO_RESOURCE: "No resource",
        NO_ACTIVE_TOKENS: "No active tokens found.",
        NO_EXPIRED_TOKENS: "No recent expired tokens.",
        EXPIRES_IN: "Expires in",
        RESOURCE: "Resource",
        CREATED_ON: "Created on",
        EXPIRED_ON: "Expired on",
        USAGE: "Usage",
        REASON: "Reason",
        LIMIT_REACHED: "Limit reached",
        TIME_ELAPSED: "Time elapsed",
        REVOKED: "Revoked",
        TOKEN_REVOKED_SUCCESS: "Token revoked successfully",
        SUSPICIOUS_USAGE: "Suspicious usage",
        SUSPICIOUS_USAGE_DETECTED: "Suspicious usage detected",
        REVOCATION_ERROR: "Error during revocation: ",
        LOADING_TOKENS: "Loading tokens...",
        LOADING_EXPIRED_TOKENS: "Loading expired tokens...",
        LOADING_ERROR: "Error loading: ",
        LOADING_EXPIRED_ERROR: "Error loading expired tokens",
        DATA_LOADING_ERROR: "Error loading data",
        REVOKING: "Revoking...",
        RETRY: "Retry",
        COPY_LINK: "Copy link",
        LINK_COPIED: "Link copied to clipboard",
        LINK_COPY_ERROR: "Could not copy link",
        REVOKE: "Revoke"
    },
    TIME_UNITS: {
        DAY: 86400,
        HOUR: 3600,
        MINUTE: 60
    },
    API_BASE: window.location.origin,
    ENDPOINTS: {
        TOKENS: '/auth/tokens',
        EXPIRED_TOKENS: '/auth/tokens/expired',
        STATS: '/auth/tokens/stats',
        REVOKE: '/auth/tokens'
    }
};

let currentTokenToRevoke = null;

// Apply translations from server configuration
if (window.PACS_CONFIG && window.PACS_CONFIG.MESSAGES) {
    Object.assign(CONFIG.MESSAGES, window.PACS_CONFIG.MESSAGES);
}

if (CONFIG.DEBUG_MODE) {
    console.log('Token Manager loaded with config:', CONFIG);
}

// Escape HTML to prevent injection
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// Format timestamp
function formatDate(timestamp) {
    if (!timestamp) return 'N/A';
    return new Date(timestamp * 1000).toLocaleString('fr-FR', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Format duration to OE2 badge
function formatDuration(seconds) {
    if (seconds < 0 || seconds === null || seconds === undefined) {
        return `<span class="duration duration-danger">${CONFIG.MESSAGES.EXPIRED}</span>`;
    }

    const days = Math.floor(seconds / CONFIG.TIME_UNITS.DAY);
    const hours = Math.floor((seconds % CONFIG.TIME_UNITS.DAY) / CONFIG.TIME_UNITS.HOUR);
    const minutes = Math.floor((seconds % CONFIG.TIME_UNITS.HOUR) / CONFIG.TIME_UNITS.MINUTE);

    let duration = '';
    if (days > 0) duration = `${days}j ${hours}h`;
    else if (hours > 0) duration = `${hours}h ${minutes}m`;
    else duration = `${minutes}m`;

    const cls = seconds > CONFIG.TIME_UNITS.DAY ? 'duration-success' :
                seconds > CONFIG.TIME_UNITS.HOUR ? 'duration-warning' :
                'duration-danger';
    return `<span class="duration ${cls}">${duration}</span>`;
}

// Resource description
function getResourceDescription(resources) {
    if (!resources || resources.length === 0) return `<span class="resource-id">${CONFIG.MESSAGES.NO_RESOURCE}</span>`;
    const resource = resources[0];
    if (resource.patient_name) {
        const meta = [];
        if (resource.modality) meta.push(escapeHtml(resource.modality));
        if (resource.study_date) meta.push(escapeHtml(resource.study_date));
        if (resource.study_description) meta.push(escapeHtml(resource.study_description));
        const metaHtml = meta.length
            ? `<span class="resource-meta">${meta.join(' · ')}</span>`
            : '';
        return `<div class="resource-cell">
            <span class="patient-name">${escapeHtml(resource.patient_name)}</span>
            ${metaHtml}
        </div>`;
    }
    const level = (resource.Level || 'study').toUpperCase();
    const id = resource.DicomUid || resource.OrthancId || 'N/A';
    const shortId = id.length > 20 ? id.substring(0, 20) + '...' : id;
    return `<span class="resource-id">${escapeHtml(level)}: ${escapeHtml(shortId)}</span>`;
}

// Suspicious usage detection
function isSuspiciousUsage(token) {
    if (!token.created_at || !token.current_uses) return false;
    const hoursElapsed = (Date.now() / 1000 - token.created_at) / 3600;
    const usageRate = token.current_uses / Math.max(hoursElapsed, 1);
    return usageRate > 10 || (token.current_uses >= 50 && hoursElapsed < 4);
}

// Expiration reason badge
function getExpirationReason(token) {
    if (token.current_uses >= token.max_uses) {
        return `<span class="badge-oe2 badge-warning">${CONFIG.MESSAGES.LIMIT_REACHED}</span>`;
    }
    if (token.remaining_seconds <= 0) {
        return `<span class="badge-oe2 badge-info">${CONFIG.MESSAGES.TIME_ELAPSED}</span>`;
    }
    return `<span class="badge-oe2 badge-danger">${CONFIG.MESSAGES.REVOKED}</span>`;
}

// Usage progress color
function getUsageColor(percent) {
    if (percent > 75) return '#dc3545';
    if (percent > 50) return '#d19b3d';
    return '#28a745';
}

// API call
async function apiCall(endpoint, method = 'GET', data = null) {
    try {
        const url = `${CONFIG.API_BASE}${endpoint}`;
        const options = {
            method,
            headers: {
                'Accept': 'application/json',
                'Cache-Control': 'no-cache'
            },
            credentials: 'include'
        };
        if (data) {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(data);
        }
        const response = await fetch(url, options);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error(`API call failed for ${endpoint}:`, error);
        throw error;
    }
}

async function fetchTokens() {
    const data = await apiCall(CONFIG.ENDPOINTS.TOKENS);
    return data.tokens || [];
}

async function fetchExpiredTokens() {
    try {
        const data = await apiCall(CONFIG.ENDPOINTS.EXPIRED_TOKENS);
        return data.tokens || [];
    } catch (error) {
        console.log('Expired tokens endpoint not available:', error);
        return [];
    }
}

async function fetchStatistics() {
    return await apiCall(CONFIG.ENDPOINTS.STATS);
}

async function revokeToken(tokenId) {
    return await apiCall(`${CONFIG.ENDPOINTS.REVOKE}/${tokenId}`, 'DELETE');
}

// Toasts
function showSuccessToast(message = CONFIG.MESSAGES.TOKEN_REVOKED_SUCCESS) {
    const toast = document.getElementById('successToast');
    toast.querySelector('.toast-body').textContent = message;
    new bootstrap.Toast(toast).show();
}

function showErrorToast(message = 'Une erreur est survenue') {
    const toast = document.getElementById('errorToast');
    document.getElementById('errorMessage').textContent = message;
    new bootstrap.Toast(toast).show();
}

// Share token types (the ones we display in the manager)
const SHARE_TOKEN_TYPES = [
    'ohif-viewer-publication',
    'stone-viewer-publication',
    'volview-viewer-publication'
];

function isShareToken(tokenType) {
    return SHARE_TOKEN_TYPES.indexOf(tokenType) !== -1;
}

// Determine token type info (only share tokens reach this point after filtering)
function getTokenTypeInfo(tokenType) {
    let label = 'Share';
    if (tokenType === 'ohif-viewer-publication') label = 'OHIF';
    else if (tokenType === 'stone-viewer-publication') label = 'Stone';
    else if (tokenType === 'volview-viewer-publication') label = 'VolView';
    return {
        badgeClass: 'badge-share',
        label: label
    };
}

// Filter tokens to only keep share tokens (ignore internal instant-view tokens)
function filterShareTokens(tokens) {
    if (!tokens) return [];
    return tokens.filter(t => isShareToken(t.token_type));
}

// Compute stats from a list of share tokens
function computeStats(tokens) {
    let total = tokens.length;
    let expiringSoon = 0;
    let highUsage = 0;
    const ONE_DAY = CONFIG.TIME_UNITS.DAY;
    tokens.forEach(t => {
        const remaining = t.remaining_seconds || 0;
        if (remaining > 0 && remaining < ONE_DAY) expiringSoon++;
        const maxUses = t.max_uses || 1;
        const currentUses = t.current_uses || 0;
        const percent = (currentUses / maxUses) * 100;
        if (percent >= 66) highUsage++;
    });
    return { total, expiringSoon, highUsage };
}

// Build active tokens table
function createTokensTableHTML(tokens) {
    if (!tokens || tokens.length === 0) {
        return `
            <div class="empty-state">
                <i class="fa-solid fa-inbox"></i>
                <p>${CONFIG.MESSAGES.NO_ACTIVE_TOKENS}</p>
            </div>
        `;
    }

    let rows = '';
    tokens.forEach(token => {
        try {
            const maxUses = token.max_uses || 1;
            const currentUses = token.current_uses || 0;
            const usagePercent = Math.round((currentUses / maxUses) * 100);
            const usageColor = getUsageColor(usagePercent);
            const typeInfo = getTokenTypeInfo(token.token_type);
            const isSuspicious = isSuspiciousUsage(token);

            rows += `
                <tr${isSuspicious ? ' class="fraud-indicator"' : ''}>
                    <td>
                        <span class="badge-oe2 ${typeInfo.badgeClass}">${typeInfo.label}</span>
                        ${isSuspicious ? `<i class="fa-solid fa-exclamation-triangle" style="color:#c44;margin-left:4px;font-size:9px;opacity:0.8;" title="${CONFIG.MESSAGES.SUSPICIOUS_USAGE}"></i>` : ''}
                    </td>
                    <td>${getResourceDescription(token.resources)}</td>
                    <td>${formatDate(token.created_at)}</td>
                    <td>${formatDuration(token.remaining_seconds)}</td>
                    <td>
                        <div class="usage-bar">
                            <div class="progress"><div class="progress-bar" style="width:${usagePercent}%;background:${usageColor};"></div></div>
                            <span class="usage-text">${currentUses}/${maxUses}</span>
                        </div>
                    </td>
                    <td>
                        <div class="actions-cell">
                            <button class="btn-action" onclick="copyShareLink('${escapeHtml(token.id)}', this)" title="${escapeHtml(CONFIG.MESSAGES.COPY_LINK)}">
                                <i class="fa-solid fa-link"></i>
                            </button>
                            <button class="btn-action btn-action-danger" onclick="confirmRevoke('${escapeHtml(token.id)}', '${escapeHtml(token.token_type || '')}')" title="${escapeHtml(CONFIG.MESSAGES.REVOKE)}">
                                <i class="fa-solid fa-trash"></i>
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        } catch (err) {
            console.error('Error rendering token row:', err, token);
        }
    });

    return `
        <table class="tokens-table">
            <thead>
                <tr>
                    <th>Type</th>
                    <th>${CONFIG.MESSAGES.RESOURCE}</th>
                    <th>${CONFIG.MESSAGES.CREATED_ON}</th>
                    <th>${CONFIG.MESSAGES.EXPIRES_IN}</th>
                    <th>${CONFIG.MESSAGES.USAGE}</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

// Build expired tokens table
function createExpiredTokensTableHTML(tokens) {
    if (!tokens || tokens.length === 0) {
        return `
            <div class="empty-state">
                <i class="fa-solid fa-clock"></i>
                <p>${CONFIG.MESSAGES.NO_EXPIRED_TOKENS}</p>
            </div>
        `;
    }

    let rows = '';
    tokens.forEach(token => {
        try {
            const maxUses = token.max_uses || 1;
            const currentUses = token.current_uses || 0;
            const usagePercent = Math.round((currentUses / maxUses) * 100);
            const usageColor = getUsageColor(usagePercent);
            const typeInfo = getTokenTypeInfo(token.token_type);
            const isSuspicious = isSuspiciousUsage(token);

            rows += `
                <tr class="expired-token${isSuspicious ? ' fraud-indicator' : ''}">
                    <td>
                        <span class="badge-oe2 ${typeInfo.badgeClass}">${typeInfo.label}</span>
                        ${isSuspicious ? `<i class="fa-solid fa-exclamation-triangle" style="color:#c44;margin-left:4px;font-size:9px;opacity:0.8;" title="${CONFIG.MESSAGES.SUSPICIOUS_USAGE_DETECTED}"></i>` : ''}
                    </td>
                    <td>${getResourceDescription(token.resources)}</td>
                    <td>${formatDate(token.created_at)}</td>
                    <td>${token.expired_at ? formatDate(token.expired_at) : 'N/A'}</td>
                    <td>
                        <div class="usage-bar">
                            <div class="progress"><div class="progress-bar" style="width:${usagePercent}%;background:${usageColor};"></div></div>
                            <span class="usage-text">${currentUses}/${maxUses}</span>
                        </div>
                    </td>
                    <td>${getExpirationReason(token)}</td>
                </tr>
            `;
        } catch (err) {
            console.error('Error rendering expired token row:', err, token);
        }
    });

    return `
        <table class="tokens-table">
            <thead>
                <tr>
                    <th>Type</th>
                    <th>${CONFIG.MESSAGES.RESOURCE}</th>
                    <th>${CONFIG.MESSAGES.CREATED_ON}</th>
                    <th>${CONFIG.MESSAGES.EXPIRED_ON}</th>
                    <th>${CONFIG.MESSAGES.USAGE}</th>
                    <th>${CONFIG.MESSAGES.REASON}</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

// Update statistics (computed client-side from share tokens only)
function updateStatistics(stats) {
    document.getElementById('totalTokens').textContent = stats.total;
    document.getElementById('expiringSoonTokens').textContent = stats.expiringSoon;
    document.getElementById('highUsageTokens').textContent = stats.highUsage;
}

// Copy share link to clipboard
async function copyShareLink(tokenId, btn) {
    const shareUrl = `${window.location.origin}/share/?token=${encodeURIComponent(tokenId)}`;
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(shareUrl);
        } else {
            const ta = document.createElement('textarea');
            ta.value = shareUrl;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
        }
        showSuccessToast(CONFIG.MESSAGES.LINK_COPIED);
        if (btn) {
            const icon = btn.querySelector('i');
            if (icon) {
                const originalClass = icon.className;
                icon.className = 'fa-solid fa-check';
                btn.classList.add('btn-action-copied');
                setTimeout(() => {
                    icon.className = originalClass;
                    btn.classList.remove('btn-action-copied');
                }, 1500);
            }
        }
    } catch (err) {
        console.error('Copy failed:', err);
        showErrorToast(CONFIG.MESSAGES.LINK_COPY_ERROR);
    }
}

// Confirm revoke
function confirmRevoke(tokenId, tokenType) {
    currentTokenToRevoke = tokenId;
    const typeInfo = getTokenTypeInfo(tokenType);

    document.getElementById('tokenDetails').innerHTML = `
        <div style="font-size:12px;">
            <strong>Token ID:</strong> <code class="token-id">${escapeHtml(tokenId)}</code><br>
            <strong style="margin-top:6px;display:inline-block;">Type:</strong>
            <span class="badge-oe2 ${typeInfo.badgeClass}">${escapeHtml(tokenType)}</span>
        </div>
    `;

    new bootstrap.Modal(document.getElementById('confirmModal')).show();
}

// Handle revoke confirmation
document.getElementById('confirmRevokeBtn').addEventListener('click', async function() {
    if (!currentTokenToRevoke) return;
    const button = this;
    const originalHTML = button.innerHTML;
    try {
        button.innerHTML = `<i class="fa-solid fa-spinner fa-spin me-1"></i>${CONFIG.MESSAGES.REVOKING}`;
        button.disabled = true;
        await revokeToken(currentTokenToRevoke);
        bootstrap.Modal.getInstance(document.getElementById('confirmModal')).hide();
        showSuccessToast();
        await loadData();
    } catch (error) {
        showErrorToast(CONFIG.MESSAGES.REVOCATION_ERROR + error.message);
    } finally {
        button.innerHTML = originalHTML;
        button.disabled = false;
        currentTokenToRevoke = null;
    }
});

// Load all data
async function loadData() {
    const container = document.getElementById('tokensContainer');
    const expiredContainer = document.getElementById('expiredTokensContainer');

    try {
        container.innerHTML = `<div class="loading-state"><i class="fa-solid fa-spinner fa-spin"></i><p>${CONFIG.MESSAGES.LOADING_TOKENS}</p></div>`;
        expiredContainer.innerHTML = `<div class="loading-state"><i class="fa-solid fa-spinner fa-spin"></i><p>${CONFIG.MESSAGES.LOADING_EXPIRED_TOKENS}</p></div>`;

        const [rawTokens, rawExpiredTokens] = await Promise.all([
            fetchTokens(),
            fetchExpiredTokens()
        ]);

        // Filter out internal instant-view tokens - keep only share tokens
        const tokens = filterShareTokens(rawTokens);
        const expiredTokens = filterShareTokens(rawExpiredTokens);

        if (CONFIG.DEBUG_MODE) {
            console.log('Raw tokens received:', rawTokens.length, rawTokens);
            console.log('Share tokens (filtered):', tokens.length, tokens);
            console.log('Raw expired tokens:', rawExpiredTokens.length);
            console.log('Expired share tokens (filtered):', expiredTokens.length);
        }

        updateStatistics(computeStats(tokens));
        container.innerHTML = createTokensTableHTML(tokens);
        expiredContainer.innerHTML = createExpiredTokensTableHTML(expiredTokens);
        document.getElementById('expiredTokensCount').textContent = `(${expiredTokens.length})`;

    } catch (error) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-exclamation-triangle" style="color:#dc3545;"></i>
                <p style="color:#dc3545;">${CONFIG.MESSAGES.LOADING_ERROR}${escapeHtml(error.message)}</p>
                <button class="btn-refresh" onclick="loadData()" style="margin-top:10px;">
                    <i class="fa-solid fa-rotate"></i>${CONFIG.MESSAGES.RETRY}
                </button>
            </div>
        `;
        expiredContainer.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-exclamation-triangle" style="color:#dc3545;"></i>
                <p style="color:#dc3545;">${CONFIG.MESSAGES.LOADING_EXPIRED_ERROR}</p>
            </div>
        `;
        showErrorToast(CONFIG.MESSAGES.DATA_LOADING_ERROR);
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    loadData();
    setInterval(loadData, CONFIG.REFRESH_INTERVAL);
});
