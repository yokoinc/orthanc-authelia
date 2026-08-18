/**
 * admin.js — frontend glue for /auth/admin.
 * Calls admin_module.py's /api/admin/* endpoints with the CSRF header.
 *
 * window.__CSRF__ is initialised in admin.html from the orthanc_admin_csrf
 * cookie the server sets while rendering the template.
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
        ['users', 'orthanc', 'modalities', 'cf', 'session', 'backups', 'health'].forEach(t => {
            document.getElementById('panel-' + t).hidden = (t !== target);
        });
        if (target === 'users') loadUsers();
        if (target === 'orthanc') loadOrthanc();
        if (target === 'modalities') loadModalities();
        if (target === 'cf') loadCF();
        if (target === 'session') loadSession();
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
                    <button class="oe2-btn oe2-btn--danger oe2-btn--sm"
                            onclick="deleteUser('${u.username}')">
                        <i class="fa-solid fa-trash"></i> Supprimer
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

// ============ ÉQUIPEMENTS ============
async function loadModalities() {
    const tbody = document.querySelector('#modalities-table tbody');
    try {
        const data = await api('/api/admin/modalities');
        tbody.innerHTML = data.modalities.map(m => `
            <tr>
                <td><strong>${m.name}</strong></td>
                <td>${m.aet}</td>
                <td>${m.host}</td>
                <td>${m.port}</td>
                <td style="text-align:right;white-space:nowrap">
                    <span id="echo-${m.name}" style="color:var(--oe2-muted);margin-right:8px"></span>
                    <button class="oe2-btn oe2-btn--sm" onclick="echoModality('${m.name}')">
                        <i class="fa-solid fa-tower-broadcast"></i> Tester
                    </button>
                    <button class="oe2-btn oe2-btn--danger oe2-btn--sm"
                            onclick="deleteModality('${m.name}')">
                        <i class="fa-solid fa-trash"></i> Supprimer
                    </button>
                </td>
            </tr>
        `).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--oe2-muted)">Aucun équipement déclaré</td></tr>';
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5">Erreur : ${e.message}</td></tr>`;
    }
}

// A silent device is a result, not an error: the route answers 200 and says
// so in its body, so we report it in place rather than as a failed call.
async function echoModality(name) {
    const cell = document.getElementById('echo-' + name);
    cell.textContent = '…';
    try {
        const r = await api(`/api/admin/modalities/${encodeURIComponent(name)}/echo`,
                            { method: 'POST' });
        cell.textContent = r.reachable ? '✓ répond' : '✗ muet';
        cell.title = r.detail || '';
        cell.style.color = r.reachable ? 'var(--oe2-ok, #4caf50)' : 'var(--oe2-danger, #e57373)';
    } catch (e) {
        cell.textContent = '✗';
        showMsg(e.message, false);
    }
}

async function deleteModality(name) {
    const ok = await confirmDialog(
        `Supprimer l'équipement "${name}" ? Il ne pourra plus envoyer d'examens.`,
        'Supprimer',
    );
    if (!ok) return;
    try {
        await api(`/api/admin/modalities/${encodeURIComponent(name)}`, { method: 'DELETE' });
        showMsg(`Équipement ${name} supprimé`, true);
        loadModalities();
    } catch (e) { showMsg(e.message, false); }
}

document.getElementById('add-modality-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const name = fd.get('name').trim();
    try {
        await api(`/api/admin/modalities/${encodeURIComponent(name)}`, {
            method: 'PUT',
            body: {
                aet: fd.get('aet').trim(),
                host: fd.get('host').trim(),
                port: Number(fd.get('port')),
            },
        });
        showMsg(`Équipement ${name} déclaré`, true);
        e.target.reset();
        e.target.port.value = 104;
        loadModalities();
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
                ? `Ecrit (backup ${data.backup}). Orthanc lit une copie faite a son `
                  + `demarrage : cliquer « Redemarrer Orthanc » pour appliquer.`
                : `Applique. Backup : ${data.backup}`,
            true,
        );
        if (data.restart_required) highlightRestart();
    } catch (err) { showMsg(err.message, false); }
});

// ============ REDEMARRAGE ORTHANC ============

// Signale qu'un redemarrage est en attente. Le bouton reste au meme endroit,
// il change seulement d'apparence : deplacer un bouton qui declenche une
// coupure du PACS serait le pire moment pour surprendre l'operateur.
function highlightRestart() {
    const btn = document.getElementById('orthanc-restart');
    if (btn) btn.classList.add('oe2-btn--primary');
}

async function restartOrthanc() {
    const ok = await confirmDialog(
        'Redemarrer Orthanc ? Le PACS sera indisponible quelques secondes. '
        + "Si la configuration l'empeche de repartir, la derniere sauvegarde "
        + 'est restauree automatiquement.',
        'Redemarrer',
    );
    if (!ok) return;

    const btn = document.getElementById('orthanc-restart');
    const initial = btn.innerHTML;
    // La route attend qu'Orthanc reponde a nouveau : jusqu'a 60 secondes. Sans
    // ce verrou l'operateur cliquerait plusieurs fois, croyant que rien ne se
    // passe, et enchainerait les redemarrages.
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-hourglass-half"></i> Redemarrage…';
    try {
        const r = await api('/api/admin/orthanc/restart', { method: 'POST' });
        showMsg(r.warning || r.message || `Orthanc ${r.version} a redemarre.`,
                !r.warning);
        btn.classList.remove('oe2-btn--primary');
    } catch (e) {
        showMsg(e.message, false);
    } finally {
        btn.disabled = false;
        btn.innerHTML = initial;
    }
}

// ============ CF ACCESS ============
async function loadCF() {
    const el = document.getElementById('cf-status');
    try {
        const d = await api('/api/admin/cf-access');
        const yes = '<span style="color:var(--oe2-success)">oui</span>';
        const no = '<span style="color:var(--oe2-danger)">non</span>';
        const warn = d.configured && d.enforced ? '' : `
            <div class="msg msg--err" style="display:block;margin-bottom:12px">
                La verification n'est pas active : les uploads ne dependent que du
                filtrage Cloudflare, sans controle a l'origine.
            </div>`;
        el.innerHTML = warn + `
            Domaine d'equipe : <code>${d.team_domain || '(non configure)'}</code><br>
            Application (aud) : <code>${d.aud_masked || '(non configure)'}</code><br>
            Verification a l'origine : ${d.configured ? yes : no}<br>
            Appliquee par nginx sur /api-upload/ : ${d.enforced ? yes : no}<br>
            Assertions acceptees : ${d.checks_ok}
        `;
        // Prefill the form with what is actually in force. The audience comes
        // back masked, so we only overwrite the field when it is still empty:
        // otherwise saving would write the ellipsis back as the real value.
        const form = document.getElementById('cf-form');
        form.team_domain.value = d.team_domain || '';
        form.enforced.checked = !!d.enforced;
        if (!form.aud.value) {
            form.aud.placeholder = d.aud_masked || "Identifiant de l'application Cloudflare";
        }
    } catch (e) {
        el.textContent = 'Erreur : ' + e.message;
    }
}

document.getElementById('cf-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const aud = (fd.get('aud') || '').trim();
    if (!aud) {
        showMsg("Saisir l'audience : elle revient masquee, donc elle doit etre "
                + 'ressaisie en entier a chaque enregistrement.', false);
        return;
    }
    try {
        await api('/api/admin/cf-access', {
            method: 'PUT',
            body: {
                team_domain: (fd.get('team_domain') || '').trim(),
                aud,
                enforced: fd.get('enforced') === 'on',
            },
        });
        showMsg('Cloudflare Access enregistre. Effet immediat, sans redemarrage.', true);
        e.target.aud.value = '';
        loadCF();
    } catch (err) { showMsg(err.message, false); }
});

// ============ SESSION ============
async function loadSession() {
    const container = document.getElementById('session-fields');
    try {
        const data = await api('/api/admin/session');
        container.innerHTML = Object.entries(data.durations).map(([key, value]) => `
            <div class="form-row">
                <label title="${data.labels[key] || ''}">${key}</label>
                <input name="${key}" value="${value ?? ''}" pattern="(\\d+[smhdwMy])+" required>
            </div>
            <div style="font-size:11px;color:var(--oe2-muted);margin:-6px 0 10px">
                ${data.labels[key] || ''}
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = `<div class="msg msg--err" style="display:block">${e.message}</div>`;
    }
}

document.getElementById('session-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {};
    fd.forEach((value, key) => { if (value) body[key] = value; });
    try {
        const data = await api('/api/admin/session', { method: 'PATCH', body });
        showMsg(
            `Ecrit (backup ${data.backup}). Authelia ne relit pas sa configuration : `
            + 'relancer le conteneur pour appliquer — docker compose restart authelia',
            true,
        );
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
                    <button class="oe2-btn oe2-btn--secondary oe2-btn--sm"
                            onclick="restoreBackup('${b.name}', '${b.target}')">
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
                  + 'cliquer « Redemarrer Orthanc » dans Configuration Orthanc pour appliquer.'
                : `${target} restaure`,
            true,
        );
        if (data.restart_required) highlightRestart();
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
