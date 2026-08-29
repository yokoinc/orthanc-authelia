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

// Les valeurs affichees viennent d'Authelia et d'orthanc.json, pas de nous, et
// elles traversent innerHTML puis des attributs onclick. Sans echappement,
// « o'brien@exemple.fr » -- une adresse parfaitement valide -- fermait la
// chaine JavaScript et le bouton Modifier de cette ligne cessait de repondre.
// Un nom affiche contenant < ou " corrompait la ligne entiere.
function echapHtml(v) {
    return String(v ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Pour une valeur placee dans une chaine JavaScript, elle-meme dans un attribut
// HTML : JSON.stringify echappe pour JavaScript (et fournit les guillemets),
// echapHtml pour l'attribut. Le parseur HTML redecode avant que JS ne lise.
function echapArg(v) {
    return echapHtml(JSON.stringify(String(v ?? '')));
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
        ['users', 'orthanc', 'modalities', 'cf', 'session', 'backups', 'audit', 'health'].forEach(t => {
            document.getElementById('panel-' + t).hidden = (t !== target);
        });
        if (target === 'users') loadUsers();
        if (target === 'orthanc') loadOrthanc();
        if (target === 'modalities') loadModalities();
        if (target === 'cf') loadCF();
        if (target === 'session') { loadNetwork(); loadSession(); }
        if (target === 'backups') loadBackups();
        if (target === 'audit') loadAudit();
        if (target === 'health') loadHealth();
    });
});

// ============ USERS ============
// Doit correspondre a ADMIN_GROUP cote auth-service. Le badge testait
// 'admins' au pluriel : il ne s'est jamais applique, l'administrateur
// s'affichait avec la pastille bleue des medecins.
const GROUPE_ADMIN = 'admin';

const MOTIF_VERROU =
    "Dernier administrateur actif : le desactiver ou le supprimer fermerait "
    + "le panneau d'administration a tout le monde, et il n'existe pas de porte "
    + "de service -- il faudrait repasser par SSH sur le NAS. Nommez un second "
    + "administrateur d'abord. Le nom et l'adresse, eux, restent modifiables.";

// Renseigne au chargement de la liste ; sert au message affiche si l'operateur
// clique quand meme sur un bouton verrouille.
let verrouMotif = '';

function expliquerVerrou() {
    showMsg(verrouMotif || MOTIF_VERROU, false);
}

async function loadUsers() {
    const tbody = document.querySelector('#users-table tbody');
    try {
        const data = await api('/api/admin/users');
        // Combien d'administrateurs peuvent encore ouvrir ce panneau. Sert a
        // verrouiller les boutons sur le dernier d'entre eux : le supprimer ou
        // le desactiver fermerait l'administration a tout le monde, et il n'y
        // a pas de porte de service -- il faudrait repasser par SSH.
        const adminsActifs = data.users.filter(
            u => (u.groups || []).includes(GROUPE_ADMIN) && !u.disabled,
        ).length;
        tbody.innerHTML = data.users.map(u => {
        const estAdmin = (u.groups || []).includes(GROUPE_ADMIN);
        // Le verrou porte sur le dernier administrateur ACTIF -- exactement la
        // meme regle que celle deja appliquee cote API (_active_admins).
        // L'interface ne fait que la rendre visible : sans cela les boutons
        // s'affichaient normalement et l'operateur recevait un refus apres coup.
        const verrouille = estAdmin && !u.disabled && adminsActifs <= 1;
        return `
            <tr>
                <td><strong>${echapHtml(u.username)}</strong></td>
                <td>${echapHtml(u.displayname)}</td>
                <td>${(u.groups || []).map(g =>
                    `<span class="badge-${g === GROUPE_ADMIN ? 'admin' : 'doctor'}">${echapHtml(g)}</span>`
                ).join(' ')}</td>
                <td>${u.disabled
                    ? '<span style="color:var(--oe2-danger)">désactivé</span>'
                    : '<span style="color:var(--oe2-success)">actif</span>'}</td>
                <td style="text-align:right;white-space:nowrap">
                    <button class="oe2-btn oe2-btn--sm" onclick="openEdit(${echapArg(u.username)})">
                        <i class="fa-solid fa-pen"></i> Modifier
                    </button>
                    <button class="oe2-btn oe2-btn--sm${verrouille ? ' btn-verrouille' : ''}"
                            ${verrouille
                              ? `onclick="expliquerVerrou()" aria-disabled="true"`
                              : `onclick="toggleDisabled(${echapArg(u.username)}, ${!!u.disabled})"`}>
                        <i class="fa-solid fa-power-off"></i> ${u.disabled ? 'Activer' : 'Désactiver'}
                    </button>
                    <button class="oe2-btn oe2-btn--danger oe2-btn--sm${verrouille ? ' btn-verrouille' : ''}"
                            ${verrouille
                              ? `onclick="expliquerVerrou()" aria-disabled="true"`
                              : `onclick="deleteUser(${echapArg(u.username)})"`}>
                        <i class="fa-solid fa-trash"></i> Supprimer
                    </button>
                </td>
            </tr>
        `;
        }).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--oe2-muted)">Aucun compte</td></tr>';
        usersCache = data.users;
        verrouMotif = adminsActifs <= 1 ? MOTIF_VERROU : '';
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5">Erreur : ${e.message}</td></tr>`;
    }
}

// Le formulaire d'edition est pre-rempli depuis la liste deja chargee, plutot
// que par un appel dedie : les valeurs affichees sont celles que l'operateur
// vient de lire, ce qui evite de lui montrer autre chose que ce qu'il a sous
// les yeux.
let usersCache = [];

function openEdit(username) {
    const u = usersCache.find(x => x.username === username);
    if (!u) return;
    const form = document.getElementById('edit-user-form');
    document.getElementById('edit-user-name').textContent = username;
    form.dataset.username = username;
    form.displayname.value = u.displayname || '';
    form.email.value = u.email || '';
    Array.from(form.groups.options).forEach(o => {
        o.selected = (u.groups || []).includes(o.value);
    });
    document.getElementById('edit-user-panel').hidden = false;
    form.displayname.focus();
}

function closeEdit() {
    document.getElementById('edit-user-panel').hidden = true;
}

document.getElementById('edit-user-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = e.target.dataset.username;
    const groups = Array.from(e.target.groups.selectedOptions).map(o => o.value);
    try {
        const res = await api(`/api/admin/users/${encodeURIComponent(username)}`, {
            method: 'PATCH',
            body: {
                displayname: e.target.displayname.value,
                email: e.target.email.value,
                groups,
            },
        });
        showMsg(
            res.renomme
                ? `Compte renomme : la connexion se fait desormais avec ${res.renomme}, `
                  + `plus avec ${username}. Le mot de passe est inchange.`
                : `${username} modifie`,
            true,
        );
        closeEdit();
        loadUsers();
    } catch (err) { showMsg(err.message, false); }
});

// Desactiver n'est pas supprimer : le compte et son historique restent, il
// cesse simplement de fonctionner. C'est ce qu'on veut quand quelqu'un s'en
// va, plutot que d'effacer sa trace.
async function toggleDisabled(username, currentlyDisabled) {
    const ok = await confirmDialog(
        currentlyDisabled
            ? `Reactiver "${username}" ? Il pourra de nouveau se connecter.`
            : `Desactiver "${username}" ? Le compte est conserve, il ne pourra `
              + 'plus se connecter.',
        currentlyDisabled ? 'Reactiver' : 'Desactiver',
    );
    if (!ok) return;
    try {
        await api(`/api/admin/users/${encodeURIComponent(username)}`, {
            method: 'PATCH',
            body: { disabled: !currentlyDisabled },
        });
        showMsg(`${username} ${currentlyDisabled ? 'reactive' : 'desactive'}`, true);
        loadUsers();
    } catch (e) { showMsg(e.message, false); }
}

async function deleteUser(username) {
    const ok = await confirmDialog(
        `Supprimer definitivement l'utilisateur "${username}" ? Un backup du fichier est conserve.`,
        'Supprimer',
    );
    if (!ok) return;
    try {
        await api(`/api/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
        showMsg(`Compte ${username} supprime`, true);
        loadUsers();
    } catch (e) { showMsg(e.message, false); }
}

// Changement de mot de passe : action distincte de la modification de fiche.
//
// La regle minimale est de DOUZE caracteres, verifiee ici et cote serveur
// (PasswordChangePayload, min_length=12). Le controle du navigateur ne protege
// rien -- il evite un aller-retour et donne un message comprehensible ; c'est
// le serveur qui decide.
//
// Cette installation n'a pas de second facteur : le mot de passe est la seule
// chose entre Internet et des images de patients. D'ou la confirmation avant
// d'agir, et la trace au journal d'audit cote serveur.
document.getElementById('edit-password-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('edit-user-form').dataset.username;
    const champ = e.target.new_password;
    const mdp = champ.value;

    if (mdp.length < 12) {
        showMsg(`Mot de passe trop court : ${mdp.length} caractere(s), il en faut 12 `
                + `au minimum. Une phrase longue vaut mieux qu'un mot complique.`, false);
        champ.focus();
        return;
    }
    if (mdp.toLowerCase() === (username || '').toLowerCase()) {
        showMsg("Le mot de passe ne peut pas etre l'adresse du compte.", false);
        champ.focus();
        return;
    }

    const ok = await confirmDialog(
        `Remplacer le mot de passe de ${username} ?

`
        + `La personne ne pourra plus se connecter avec l'ancien, et devra `
        + `utiliser le nouveau que vous venez de saisir.`,
        'Changer le mot de passe',
    );
    if (!ok) return;

    try {
        await api(`/api/admin/users/${encodeURIComponent(username)}/password`, {
            method: 'PATCH',
            body: { new_password: mdp },
        });
        champ.value = '';
        showMsg(`Mot de passe de ${username} remplace. Transmettez-le a la personne `
                + `par un canal sur : il n'est affiche nulle part et ne peut pas etre relu.`, true);
    } catch (err) { showMsg(err.message, false); }
});

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
                <td><strong>${echapHtml(m.name)}</strong></td>
                <td>${echapHtml(m.aet)}</td>
                <td>${echapHtml(m.host)}</td>
                <td>${m.port}</td>
                <td style="text-align:right;white-space:nowrap">
                    <span id="echo-${echapHtml(m.name)}" style="color:var(--oe2-muted);margin-right:8px"></span>
                    <button class="oe2-btn oe2-btn--sm" onclick="echoModality(${echapArg(m.name)})">
                        <i class="fa-solid fa-tower-broadcast"></i> Tester
                    </button>
                    <button class="oe2-btn oe2-btn--danger oe2-btn--sm"
                            onclick="deleteModality(${echapArg(m.name)})">
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

/**
 * Rend un « ? » cliquable portant l'explication d'un reglage.
 *
 * L'onglet Orthanc affichait le nom brut de la cle et rien d'autre.
 * "DicomAlwaysAllowStore" ou "StableAge" ne disent rien a qui n'a pas lu la
 * documentation d'Orthanc -- et un PACS se regle rarement par un specialiste
 * d'Orthanc.
 *
 * Le texte passe par title= plutot que par une infobulle maison : il survit au
 * clavier, au lecteur d'ecran et a la copie, ce qu'une div positionnee ne fait
 * pas gratuitement. tabindex le rend atteignable sans souris.
 *
 * Echappement obligatoire : ces textes viennent du serveur et finissent dans
 * un attribut HTML.
 */
function aide(texte) {
    if (!texte) return '';
    const t = String(texte)
        .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;');
    // data-aide plutot que title : l'infobulle native tarde une seconde a
    // sortir, s'efface toute seule, et ne s'affichait pas du tout ici. La bulle
    // est donc dessinee en CSS (.aide::after) -- instantanee et lisible.
    return ` <span class="aide" tabindex="0" role="note" data-aide="${t}"
                   aria-label="Explication : ${t}">?</span>`;
}

// ============ ORTHANC CONFIG ============
// Valeurs telles que lues au chargement de l'onglet. L'enregistrement s'y
// compare pour n'envoyer que ce qui a reellement change.
let orthancCharge = {};


// Ce qu'Orthanc applique quand le reglage est absent du fichier.
//
// « non defini » etait exact et inutile : l'operateur veut savoir ce que fait
// le serveur, pas ce que le fichier tait. Les valeurs viennent du serveur
// (ORTHANC_DEFAUTS), extraites de la configuration de reference qu'Orthanc
// emet lui-meme -- elles correspondent donc a la version installee.
function texteDefaut(cle, defauts) {
    var d = defauts && Object.prototype.hasOwnProperty.call(defauts, cle)
            ? defauts[cle] : undefined;
    if (d === undefined || d === null) {
        // Les DicomWeb.* n'ont pas de defaut connu de nous : leurs valeurs
        // appartiennent au greffon. Mieux vaut ne rien annoncer que d'inventer.
        return 'non défini';
    }
    if (Array.isArray(d)) return 'non défini — Orthanc applique : ' + d.join(', ');
    return 'non défini — Orthanc applique : ' + d;
}

async function loadOrthanc() {
    const container = document.getElementById('orthanc-fields');
    try {
        const data = await api('/api/admin/orthanc/config');
        orthancCharge = data.editable;   // reference pour le diff a l'enregistrement
        container.innerHTML = Object.entries(data.editable).map(([key, value]) => {
            const inputId = 'orth-' + key.replace(/\./g, '_');
            let control;
            // Le type vient du serveur, pas de la valeur. Un reglage ABSENT
            // d'orthanc.json arrive a null : `typeof null === 'object'`, et le
            // test precedent (`typeof value === 'boolean' || value === null`)
            // attrapait donc TOUS les absents en menu true/false. DicomScpTimeout
            // et DicomThreadsCount, qui sont des entiers, s'affichaient ainsi en
            // booleens -- et enregistrer y aurait ecrit `true`.
            const type = data.types?.[key] || (value === null ? 'str' : typeof value);
            if (type === 'bool' || typeof value === 'boolean') {
                control = `<select id="${inputId}" data-key="${key}">
                    <option value="" ${value === null ? 'selected' : ''}>(${texteDefaut(key, data.defauts)})</option>
                    <option value="true" ${value === true ? 'selected' : ''}>true</option>
                    <option value="false" ${value === false ? 'selected' : ''}>false</option>
                </select>`;
            } else if (type === 'int' || typeof value === 'number') {
                control = `<input type="number" id="${inputId}" data-key="${key}" value="${value ?? ''}"
                                  placeholder="${texteDefaut(key, data.defauts)}">`;
            } else {
                control = `<input type="text" id="${inputId}" data-key="${key}" value="${value ?? ''}"
                                  placeholder="${texteDefaut(key, data.defauts)}">`;
            }
            return `<div class="form-row"><label for="${inputId}">${key}${aide(data.aide?.[key])}</label>${control}</div>`;
        }).join('');
    } catch (e) {
        container.innerHTML = `<div class="msg msg--err" style="display:block">${e.message}</div>`;
    }
    loadDivergences();
}

// Ce que le fichier declare n'est pas forcement ce qu'Orthanc applique : une
// variable ORTHANC__* du compose peut l'ecraser, ou le redemarrage n'a jamais
// eu lieu. Sans cet affichage l'operateur lit ses valeurs dans le formulaire
// et croit qu'elles tournent.
async function loadDivergences() {
    const zone = document.getElementById('orthanc-divergences');
    if (!zone) return;
    zone.innerHTML = '';
    try {
        const d = await api('/api/admin/config-effective');
        if (!d.mismatches.length) return;
        zone.innerHTML = `
            <div class="msg msg--err" style="display:block">
                <strong>${d.mismatches.length} reglage(s) ne sont pas appliques
                tels qu'ecrits.</strong> Une variable ORTHANC__* du compose les
                ecrase peut-etre, ou Orthanc n'a pas redemarre depuis la
                derniere modification.
                <table class="data-table" style="margin-top:8px">
                    <thead><tr><th>Reglage</th><th>Dans le fichier</th><th>Applique</th></tr></thead>
                    <tbody>${d.mismatches.map(m => `
                        <tr><td><strong>${m.field}</strong></td>
                            <td>${JSON.stringify(m.in_file)}</td>
                            <td>${JSON.stringify(m.applied_by_orthanc)}</td></tr>
                    `).join('')}</tbody>
                </table>
            </div>`;
    } catch {
        // Orthanc injoignable : /health le dit deja, ne pas doubler l'alerte.
    }
}

document.getElementById('orthanc-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const changes = {};
    document.querySelectorAll('#orthanc-fields [data-key]').forEach(input => {
        const key = input.dataset.key;
        const brut = input.value;
        // Un champ vide veut dire « pas defini dans orthanc.json », pas « zero ».
        // L'ancienne boucle envoyait TOUS les champs et convertissait le vide en
        // 0 : ouvrir cet onglet puis cliquer Enregistrer suffisait a ecrire
        // MaximumStorageSize: 0, DicomScpTimeout: 0 et false sur une quinzaine
        // de reglages jamais touches.
        if (brut === '') return;
        let val = brut;
        if (input.tagName === 'SELECT') val = (brut === 'true');
        else if (input.type === 'number') val = Number(val);
        // Et on n'envoie que les differences : reecrire a l'identique creerait
        // une sauvegarde et reclamerait un redemarrage d'Orthanc pour rien.
        if (val === orthancCharge[key]) return;
        changes[key] = val;
    });
    if (Object.keys(changes).length === 0) {
        showMsg('Aucune modification.', true);
        return;
    }
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

// ============ ADRESSE PUBLIQUE ============

// Changer ce domaine touche .env et onze endroits de la configuration
// d'Authelia. Le faire a la main veut dire tous les reussir : en manquer un
// laisse Authelia repondre 401 partout, page de connexion comprise, et plus
// rien dans cette interface ne sait le reparer.
async function loadNetwork() {
    const note = document.getElementById('network-note');
    try {
        const d = await api('/api/admin/network');
        document.getElementById('network-form').public_url.value = d.public_url || '';
        const bouton = document.querySelector('#network-form button[type=submit]');
        const champ = document.getElementById('network-form').public_url;
        if (!d.editable) {
            champ.disabled = true;
            bouton.disabled = true;
            note.textContent = "Modification indisponible : le fichier .env n'est pas "
                + "monte dans le conteneur. Ajouter './.env:/host/env/.env:rw' au "
                + 'service auth-service, puis recreer le conteneur.';
        } else {
            champ.disabled = false;
            bouton.disabled = false;
            note.textContent = 'Le changement prend effet au redemarrage de la pile, '
                + 'et impose de se reconnecter a la nouvelle adresse : le cookie de '
                + "session est lie a l'ancien domaine.";
        }
    } catch (e) {
        note.textContent = 'Erreur : ' + e.message;
    }
}

document.getElementById('network-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const url = e.target.public_url.value.trim();
    const ok = await confirmDialog(
        `Faire pointer le PACS sur ${url} ? Authelia et .env sont reecrits, une `
        + 'sauvegarde est prise avant. Il faudra redemarrer la pile et se '
        + 'reconnecter a la nouvelle adresse.',
        'Changer',
    );
    if (!ok) return;
    try {
        const r = await api('/api/admin/network', {
            method: 'POST', body: { public_url: url },
        });
        showMsg(r.unchanged
            ? 'Adresse inchangee, rien de reecrit.'
            : `${r.substitutions} occurrence(s) mises a jour. Redemarrer la pile `
              + 'pour appliquer.', true);
        loadNetwork();
    } catch (err) { showMsg(err.message, false); }
});

// ============ SESSION ============
async function loadSession() {
    const container = document.getElementById('session-fields');
    try {
        const data = await api('/api/admin/session');
        container.innerHTML = Object.entries(data.durations).map(([key, value]) => `
            <div class="form-row">
                <label for="sess-${key}">${key}${aide(data.labels[key])}</label>
                <input id="sess-${key}" name="${key}" value="${value ?? ''}"
                       pattern="(\\d+[smhdwMy])+" required>
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

// ============ JOURNAL ============

// Le flux etait alimente depuis le premier jour sans que rien ne le lise.
let auditCache = [];

async function loadAudit() {
    const tbody = document.querySelector('#audit-table tbody');
    try {
        const d = await api('/api/admin/audit?limit=200');
        auditCache = d.entries;
        renderAudit();
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4">Erreur : ${e.message}</td></tr>`;
    }
}

function renderAudit() {
    const tbody = document.querySelector('#audit-table tbody');
    const filtre = (document.getElementById('audit-filter').value || '').toLowerCase();
    const lignes = auditCache.filter(e =>
        !filtre
        || e.event.toLowerCase().includes(filtre)
        || e.actor.toLowerCase().includes(filtre)
        || JSON.stringify(e.details).toLowerCase().includes(filtre)
    );
    tbody.innerHTML = lignes.map(e => `
        <tr>
            <td style="white-space:nowrap">${new Date(e.ts * 1000).toLocaleString()}</td>
            <td><strong>${e.event}</strong></td>
            <td>${e.actor}</td>
            <td style="color:var(--oe2-muted)">${
                Object.entries(e.details).map(([k, v]) => `${k} : ${v}`).join(' · ')
            }</td>
        </tr>
    `).join('') || `<tr><td colspan="4" style="text-align:center;color:var(--oe2-muted)">${
        filtre ? 'Aucun evenement ne correspond' : 'Journal vide'}</td></tr>`;
}

// ============ SAUVEGARDE MANUELLE ============

// Les copies n'etaient prises qu'en reaction a une ecriture du panel : prendre
// un point de restauration AVANT une operation risquee etait impossible, alors
// que c'est precisement le moment ou on le veut.
async function createBackup() {
    const btn = document.getElementById('backup-now');
    btn.disabled = true;
    try {
        const r = await api('/api/admin/backups', { method: 'POST' });
        showMsg(`${r.created.length} fichier(s) sauvegarde(s).`, true);
        loadBackups();
    } catch (e) {
        showMsg(e.message, false);
    } finally {
        btn.disabled = false;
    }
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
