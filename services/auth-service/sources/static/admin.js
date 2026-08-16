/**
 * admin.js — glue frontend pour /auth/admin.
 * Appelle les endpoints /api/admin/* de admin_module.py avec le header CSRF.
 *
 * window.__CSRF__ est initialise dans admin.html depuis le cookie
 * orthanc_admin_csrf pose par le serveur au rendu du template.
 */

function api(path, opts) {
    opts = opts || {};
    opts.credentials = 'same-origin';
    opts.headers = Object.assign({
        'content-type': 'application/json',
        'x-csrf-token': window.__CSRF__ || '',
    }, opts.headers || {});
    if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
    return fetch(path, opts).then(async r => {
        const text = await r.text();
        let data;
        try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
        if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
        return data;
    });
}

/**
 * In-page confirmation. Replaces window.confirm(), whose native box ignores the
 * page styling and cannot be worded properly. Resolves true/false.
 * Escape and the backdrop both cancel, so the destructive path always needs an
 * explicit click.
 */
function confirmDialog(message, okLabel) {
    const backdrop = document.getElementById('confirm-backdrop');
    const ok = document.getElementById('confirm-ok');
    const cancel = document.getElementById('confirm-cancel');
    document.getElementById('confirm-text').textContent = message;
    ok.textContent = okLabel || 'Confirmer';
    backdrop.hidden = false;
    ok.focus();

    return new Promise(resolve => {
        function close(result) {
            backdrop.hidden = true;
            ok.removeEventListener('click', onOk);
            cancel.removeEventListener('click', onCancel);
            backdrop.removeEventListener('click', onBackdrop);
            document.removeEventListener('keydown', onKey);
            resolve(result);
        }
        function onOk() { close(true); }
        function onCancel() { close(false); }
        function onBackdrop(e) { if (e.target === backdrop) close(false); }
        function onKey(e) { if (e.key === 'Escape') close(false); }

        ok.addEventListener('click', onOk);
        cancel.addEventListener('click', onCancel);
        backdrop.addEventListener('click', onBackdrop);
        document.addEventListener('keydown', onKey);
    });
}

function showMsg(text, ok) {
    const el = document.getElementById('global-msg');
    el.textContent = text;
    el.className = 'msg msg--' + (ok ? 'ok' : 'err');
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 4000);
}

// ============ Tabs ============
document.querySelectorAll('.admin-tab').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.admin-tab').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const target = btn.dataset.tab;
        ['users', 'orthanc', 'cf', 'backups', 'health'].forEach(t => {
            document.getElementById('panel-' + t).hidden = (t !== target);
        });
        if (target === 'users') loadUsers();
        if (target === 'orthanc') loadOrthanc();
        if (target === 'cf') loadCF();
        if (target === 'backups') loadBackups();
        if (target === 'health') loadHealth();
    });
});

// ============ USERS ============
async function loadUsers() {
    const tbody = document.querySelector('#users-table tbody');
    try {
        const data = await api('/api/admin/users');
        tbody.innerHTML = data.users.map(u => `
            <tr>
                <td><strong>${u.username}</strong></td>
                <td>${u.displayname || ''}</td>
                <td>${u.email || ''}</td>
                <td>${(u.groups || []).map(g =>
                    `<span class="badge-${g === 'admins' ? 'admin' : 'doctor'}">${g}</span>`
                ).join(' ')}</td>
                <td style="text-align:right">
                    <button class="oe2-btn oe2-btn--sm" onclick="deleteUser('${u.username}')">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </td>
            </tr>
        `).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--oe2-muted)">Aucun user</td></tr>';
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5">Erreur : ${e.message}</td></tr>`;
    }
}

async function deleteUser(username) {
    const ok = await confirmDialog(
        `Supprimer definitivement l'utilisateur "${username}" ? Un backup du fichier est conserve.`,
        'Supprimer',
    );
    if (!ok) return;
    try {
        await api(`/api/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
        showMsg(`User ${username} supprime`, true);
        loadUsers();
    } catch (e) { showMsg(e.message, false); }
}

document.getElementById('add-user-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const groups = Array.from(e.target.groups.selectedOptions).map(o => o.value);
    try {
        // No login field: the e-mail is the identity, it is the key Authelia
        // matches in users_database.yml. The server derives it.
        await api('/api/admin/users', {
            method: 'POST',
            body: {
                displayname: fd.get('displayname'),
                email: fd.get('email'),
                password: fd.get('password'),
                groups,
            },
        });
        showMsg('Compte cree. Authelia relit le fichier automatiquement.', true);
        e.target.reset();
        loadUsers();
    } catch (err) { showMsg(err.message, false); }
});

// ============ ORTHANC CONFIG ============
async function loadOrthanc() {
    const container = document.getElementById('orthanc-fields');
    try {
        const data = await api('/api/admin/orthanc/config');
        container.innerHTML = Object.entries(data.editable).map(([key, value]) => {
            const inputId = 'orth-' + key.replace(/\./g, '_');
            let control;
            if (typeof value === 'boolean' || value === null) {
                control = `<select id="${inputId}" data-key="${key}">
                    <option value="true" ${value === true ? 'selected' : ''}>true</option>
                    <option value="false" ${value === false ? 'selected' : ''}>false</option>
                </select>`;
            } else if (typeof value === 'number' || value === null) {
                control = `<input type="number" id="${inputId}" data-key="${key}" value="${value ?? ''}">`;
            } else {
                control = `<input type="text" id="${inputId}" data-key="${key}" value="${value ?? ''}">`;
            }
            return `<div class="form-row"><label>${key}</label>${control}</div>`;
        }).join('');
    } catch (e) {
        container.innerHTML = `<div class="msg msg--err" style="display:block">${e.message}</div>`;
    }
}

document.getElementById('orthanc-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const changes = {};
    document.querySelectorAll('#orthanc-fields [data-key]').forEach(input => {
        const key = input.dataset.key;
        let val = input.value;
        if (input.tagName === 'SELECT') val = (val === 'true');
        else if (input.type === 'number') val = val === '' ? 0 : Number(val);
        changes[key] = val;
    });
    try {
        const data = await api('/api/admin/orthanc/config', {
            method: 'PATCH',
            body: { changes },
        });
        // The server tells us whether Orthanc actually picked the change up.
        // Saying "applied" when it only got written would be a lie.
        showMsg(
            data.restart_required
                ? `Ecrit (backup ${data.backup}). Orthanc lit une copie faite a son demarrage : `
                  + `relancer le conteneur pour appliquer — docker compose restart orthanc`
                : `Applique. Backup : ${data.backup}`,
            true,
        );
    } catch (err) { showMsg(err.message, false); }
});

// ============ CF ACCESS ============
async function loadCF() {
    try {
        const data = await api('/api/admin/cf-access');
        document.getElementById('cf-status').innerHTML = `
            Client ID actuel : <code>${data.client_id_masked || '(non configure)'}</code><br>
            Secret configure : ${data.secret_configured ? '<span style="color:var(--oe2-success)">oui</span>' : '<span style="color:var(--oe2-danger)">non</span>'}<br>
            Rotations historisees : ${data.history_length}
        `;
    } catch (e) {
        document.getElementById('cf-status').textContent = 'Erreur : ' + e.message;
    }
}

document.getElementById('cf-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const ok = await confirmDialog(
        'Rotation atomique des identifiants CF Access. Effet immediat sur la prochaine '
        + 'requete /api-upload/ : tout client utilisant l\'ancien secret sera refuse.',
        'Effectuer la rotation',
    );
    if (!ok) return;
    const fd = new FormData(e.target);
    try {
        await api('/api/admin/cf-access/rotate', {
            method: 'POST',
            body: {
                client_id: fd.get('client_id'),
                client_secret: fd.get('client_secret'),
            },
        });
        showMsg('Rotation OK, effet immediat', true);
        e.target.reset();
        loadCF();
    } catch (err) { showMsg(err.message, false); }
});

// ============ BACKUPS ============
function formatBytes(n) {
    return n < 1024 ? n + ' o' : (n / 1024).toFixed(1) + ' ko';
}

async function loadBackups() {
    const tbody = document.querySelector('#backups-table tbody');
    try {
        const data = await api('/api/admin/backups');
        if (!data.backups.length) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--oe2-muted)">'
                + 'Aucune sauvegarde pour le moment</td></tr>';
            return;
        }
        tbody.innerHTML = data.backups.map(b => {
            const when = new Date(b.modified * 1000).toLocaleString('fr-FR');
            return `
            <tr>
                <td style="white-space:nowrap">${when}</td>
                <td><strong>${b.target}</strong><br>
                    <span style="font-family:monospace;font-size:11px;color:var(--oe2-muted)">
                        ${b.name} — ${formatBytes(b.size)}
                    </span></td>
                <td style="font-size:12px">${b.detail || ''}</td>
                <td style="text-align:right;white-space:nowrap">
                    <button class="oe2-btn oe2-btn--sm" onclick="restoreBackup('${b.name}', '${b.target}')">
                        <i class="fa-solid fa-clock-rotate-left"></i> Restaurer
                    </button>
                </td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4">Erreur : ${e.message}</td></tr>`;
    }
}

async function restoreBackup(name, target) {
    const ok = await confirmDialog(
        `Restaurer ${target} depuis ${name} ? Le contenu actuel sera remplace, `
        + 'mais il est sauvegarde au prealable : l\'operation reste reversible.',
        'Restaurer',
    );
    if (!ok) return;
    try {
        const data = await api(
            `/api/admin/backups/restore?backup_name=${encodeURIComponent(name)}`,
            { method: 'POST' },
        );
        showMsg(
            data.restart_required
                ? `${target} restaure. Orthanc lit une copie faite a son demarrage : `
                  + 'relancer le conteneur pour appliquer — docker compose restart orthanc'
                : `${target} restaure`,
            true,
        );
        loadBackups();
        if (target === 'users_database.yml') loadUsers();
    } catch (e) { showMsg(e.message, false); }
}

// ============ HEALTH ============
async function loadHealth() {
    const tbody = document.querySelector('#health-table tbody');
    try {
        const data = await api('/api/admin/health');
        tbody.innerHTML = Object.entries(data.checks).map(([name, info]) => `
            <tr>
                <td><strong>${name}</strong></td>
                <td>${info.ok
                    ? '<span style="color:var(--oe2-success)"><i class="fa-solid fa-check"></i> OK</span>'
                    : '<span style="color:var(--oe2-danger)"><i class="fa-solid fa-xmark"></i> KO</span>'}</td>
                <td style="font-family:monospace;font-size:11px;color:var(--oe2-muted)">${info.detail}</td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="3">Erreur : ${e.message}</td></tr>`;
    }
}

// ============ Init ============
function initAdmin() {
    loadUsers();
}
