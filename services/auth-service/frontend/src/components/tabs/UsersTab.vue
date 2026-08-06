<script setup>
import { ref, reactive, onMounted } from 'vue'
import { api } from '../../api.js'
import { t } from '../../i18n.js'
import { useUiStore } from '../../stores/ui.js'

const ui = useUiStore()
const users = ref([])
const loading = ref(true)
const showAddForm = ref(false)
const newUser = reactive({
  username: '', displayname: '', email: '', password: '',
  groups: ['doctor'],
})

async function load() {
  loading.value = true
  try {
    const data = await api('/console/api/admin/users')
    users.value = data.users
  } catch (e) {
    ui.notify(t('users_load_error', 'Erreur au chargement des utilisateurs : {detail}', { detail: e.message }), 'err')
  } finally {
    loading.value = false
  }
}

async function addUser() {
  try {
    await api('/console/api/admin/users', { method: 'POST', body: { ...newUser } })
    ui.notify(t('users_created', 'Utilisateur créé. Authelia le prendra en compte dans quelques secondes.'), 'ok')
    Object.assign(newUser, {
      username: '', displayname: '', email: '', password: '',
      groups: ['doctor'],
    })
    showAddForm.value = false
    load()
  } catch (e) {
    ui.notify(e.message, 'err')
  }
}

// Modification d'un compte : meme principe de ligne depliante que le mot de
// passe, pour ne pas quitter le tableau.
const editionPour = ref('')
const edition = reactive({ displayname: '', email: '', groups: [], disabled: false })
const envoiEdition = ref(false)

function ouvrirEdition(u) {
  if (editionPour.value === u.username) {
    editionPour.value = ''
    return
  }
  editionPour.value = u.username
  motDePassePour.value = ''
  Object.assign(edition, {
    displayname: u.displayname || '',
    email: u.email || '',
    groups: [...(u.groups || [])],
    disabled: !!u.disabled,
  })
}

function basculerGroupeEdition(g) {
  const i = edition.groups.indexOf(g)
  if (i >= 0) edition.groups.splice(i, 1)
  else edition.groups.push(g)
}

async function enregistrerEdition() {
  envoiEdition.value = true
  try {
    await api(`/console/api/admin/users/${encodeURIComponent(editionPour.value)}`, {
      method: 'PATCH',
      body: {
        displayname: edition.displayname,
        email: edition.email,
        groups: edition.groups,
        disabled: edition.disabled,
      },
    })
    ui.notify(t('users_updated', '{username} a été modifié.', { username: editionPour.value }), 'ok')
    editionPour.value = ''
    load()
  } catch (e) {
    // Le serveur refuse de laisser la pile sans administrateur actif : le
    // message explique ce qui bloque, il ne faut pas le masquer.
    ui.notify(e.message, 'err')
  } finally {
    envoiEdition.value = false
  }
}

// Changement de mot de passe : saisie en ligne plutot qu'un prompt(), qui
// afficherait le mot de passe en clair et ne permet aucune validation.
const motDePassePour = ref('')
const nouveauMotDePasse = ref('')
const envoiMotDePasse = ref(false)

function ouvrirMotDePasse(username) {
  motDePassePour.value = motDePassePour.value === username ? '' : username
  nouveauMotDePasse.value = ''
}

async function enregistrerMotDePasse() {
  if (nouveauMotDePasse.value.length < 12) return
  envoiMotDePasse.value = true
  try {
    await api(`/console/api/admin/users/${encodeURIComponent(motDePassePour.value)}/password`, {
      method: 'PATCH',
      body: { new_password: nouveauMotDePasse.value },
    })
    ui.notify(t('users_password_changed', 'Mot de passe modifié pour {username}.', { username: motDePassePour.value }), 'ok')
    motDePassePour.value = ''
    nouveauMotDePasse.value = ''
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    envoiMotDePasse.value = false
  }
}

async function deleteUser(username) {
  if (!confirm(t('users_delete_confirm', 'Supprimer l\'utilisateur « {username} » ?', { username }))) return
  try {
    await api(`/console/api/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' })
    ui.notify(t('users_deleted', '{username} a été supprimé.', { username }), 'ok')
    load()
  } catch (e) {
    ui.notify(e.message, 'err')
  }
}

function toggleGroup(g) {
  const i = newUser.groups.indexOf(g)
  if (i >= 0) newUser.groups.splice(i, 1)
  else newUser.groups.push(g)
}

onMounted(load)
</script>

<template>
  <div>
    <h2>{{ t('users_title', 'Utilisateurs') }}</h2>

    <div v-if="loading" class="loading">{{ t('loading', 'Chargement…') }}</div>

    <table v-else class="table">
      <thead>
        <tr>
          <th>{{ t('users_col_login', 'Identifiant') }}</th>
          <th>{{ t('users_col_name', 'Nom') }}</th>
          <th>{{ t('users_col_email', 'Adresse e-mail') }}</th>
          <th>{{ t('users_col_groups', 'Groupes') }}</th>
          <th>{{ t('users_col_status', 'Statut') }}</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <template v-for="u in users" :key="u.username">
        <tr>
          <td><strong>{{ u.username }}</strong></td>
          <td>{{ u.displayname }}</td>
          <td>{{ u.email }}</td>
          <td>
            <span
              v-for="g in u.groups"
              :key="g"
              :class="['badge', g === 'admin' ? 'badge--admin' : 'badge--doctor']"
            >{{ g }}</span>
          </td>
          <td>
            <span :class="u.disabled ? 'etat etat--off' : 'etat etat--on'">
              {{ u.disabled ? t('users_disabled', 'Désactivé') : t('users_enabled', 'Actif') }}
            </span>
          </td>
          <td class="right">
            <button
              class="oe2-btn oe2-btn--secondary"
              :title="t('users_edit', 'Modifier')"
              @click="ouvrirEdition(u)"
            >
              <i class="fa-solid fa-pen"></i>
            </button>
            <button
              class="oe2-btn oe2-btn--secondary"
              :title="t('users_change_password', 'Changer le mot de passe')"
              @click="ouvrirMotDePasse(u.username)"
            >
              <i class="fa-solid fa-key"></i>
            </button>
            <button class="oe2-btn oe2-btn--danger" @click="deleteUser(u.username)" :title="t('delete', 'Supprimer')">
              <i class="fa-solid fa-trash"></i>
            </button>
          </td>
        </tr>
        <tr v-if="editionPour === u.username">
          <td colspan="6" class="edit-ligne">
            <div class="champ">
              <label>{{ t('setup_displayname_label', 'Nom affiché') }}</label>
              <input v-model="edition.displayname" maxlength="100">
            </div>
            <div class="champ">
              <label>{{ t('setup_email_label', 'Adresse e-mail') }}</label>
              <input v-model="edition.email" type="email">
            </div>
            <div class="champ">
              <label>{{ t('users_col_groups', 'Groupes') }}</label>
              <div class="groups">
                <label v-for="g in ['admin', 'doctor', 'external']" :key="g" class="chk">
                  <input
                    type="checkbox" :checked="edition.groups.includes(g)"
                    @change="basculerGroupeEdition(g)"
                  >
                  {{ g }}
                </label>
              </div>
            </div>
            <div class="champ">
              <label class="chk">
                <input type="checkbox" v-model="edition.disabled">
                {{ t('users_disable', 'Désactiver ce compte') }}
              </label>
            </div>
            <div class="champ">
              <button
                class="oe2-btn oe2-btn--primary"
                :disabled="envoiEdition"
                @click="enregistrerEdition"
              >
                {{ envoiEdition ? t('saving', 'Enregistrement…') : t('save', 'Enregistrer') }}
              </button>
              <button class="oe2-btn oe2-btn--ghost" @click="editionPour = ''">
                {{ t('cancel', 'Annuler') }}
              </button>
            </div>
          </td>
        </tr>
        <tr v-if="motDePassePour === u.username">
          <td colspan="6" class="pwd-ligne">
            <label>{{ t('users_new_password', 'Nouveau mot de passe') }}</label>
            <input
              v-model="nouveauMotDePasse" type="password" minlength="12"
              :placeholder="t('users_password_hint', '12 caractères minimum')"
              @keyup.enter="enregistrerMotDePasse"
            >
            <button
              class="oe2-btn oe2-btn--primary"
              :disabled="nouveauMotDePasse.length < 12 || envoiMotDePasse"
              @click="enregistrerMotDePasse"
            >
              {{ envoiMotDePasse ? t('saving', 'Enregistrement…') : t('save', 'Enregistrer') }}
            </button>
            <button class="oe2-btn oe2-btn--ghost" @click="ouvrirMotDePasse('')">
              {{ t('cancel', 'Annuler') }}
            </button>
          </td>
        </tr>
        </template>
        <tr v-if="!users.length">
          <td colspan="6" class="loading">{{ t('users_empty', 'Aucun utilisateur') }}</td>
        </tr>
      </tbody>
    </table>

    <details :open="showAddForm" @toggle="showAddForm = $event.target.open">
      <summary>{{ t('users_add', '+ Ajouter un utilisateur') }}</summary>
      <form class="add-form" @submit.prevent="addUser">
        <div class="row"><label>{{ t('users_col_login', 'Identifiant') }}</label><input v-model="newUser.username" required pattern="[a-zA-Z0-9._-]{3,32}"></div>
        <div class="row"><label>{{ t('setup_displayname_label', 'Nom affiché') }}</label><input v-model="newUser.displayname" required></div>
        <div class="row"><label>{{ t('setup_email_label', 'Adresse e-mail') }}</label><input v-model="newUser.email" type="email" required></div>
        <div class="row"><label>{{ t('setup_password_label', 'Mot de passe') }}</label><input v-model="newUser.password" type="password" required minlength="12"></div>
        <div class="row">
          <label>{{ t('users_col_groups', 'Groupes') }}</label>
          <div class="groups">
            <label v-for="g in ['admin', 'doctor', 'external']" :key="g" class="chk">
              <input type="checkbox" :checked="newUser.groups.includes(g)" @change="toggleGroup(g)">
              {{ g }}
            </label>
          </div>
        </div>
        <button type="submit" class="oe2-btn oe2-btn--primary">{{ t('create', 'Créer') }}</button>
      </form>
    </details>
  </div>
</template>

<style scoped>
.etat { font-size: 11px; }
.etat--on { color: var(--oe2-success); }
.etat--off { color: var(--oe2-muted); }
.edit-ligne {
  background: var(--oe2-nav-sub-bg);
  display: flex; align-items: flex-end; gap: 14px; flex-wrap: wrap;
}
.edit-ligne .champ { display: flex; flex-direction: column; gap: 3px; }
.edit-ligne label { color: var(--oe2-muted); font-size: 11px; }
.edit-ligne input[type=text], .edit-ligne input[type=email], .edit-ligne input:not([type]) {
  background: var(--oe2-nav-bg);
  border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color);
  padding: 5px 8px; border-radius: 3px;
  font-family: var(--oe2-font-stack); font-size: var(--oe2-fs-small);
}
.pwd-ligne {
  background: var(--oe2-nav-sub-bg);
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}
.pwd-ligne label { color: var(--oe2-muted); font-size: 11px; }
.pwd-ligne input {
  background: var(--oe2-nav-bg);
  border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color);
  padding: 5px 8px; border-radius: 3px;
  font-family: var(--oe2-font-stack); font-size: var(--oe2-fs-small);
}
h2 { font-size: 14px; margin: 0 0 12px; font-weight: 400; }
.loading { color: var(--oe2-muted); text-align: center; padding: 20px; }
.table { width: 100%; border-collapse: collapse; font-size: 12px; }
.table th, .table td {
  padding: 6px 10px; text-align: left;
  /* Bleu clair comme les tableaux d'Orthanc, cf. --oe2-table-border. */
  border-bottom: 1px solid var(--oe2-table-border);
}
.table th {
  color: var(--oe2-muted); text-transform: uppercase;
  letter-spacing: 0.5px; font-weight: 400; font-size: 11px;
}
.right { text-align: right; }
.badge {
  display: inline-block; padding: 2px 6px; border-radius: 2px;
  font-size: 10px; text-transform: uppercase; margin-right: 4px;
}
.badge--admin { background: var(--oe2-accent-orange); color: white; }
.badge--doctor { background: var(--oe2-label-bg); color: white; }
details { margin-top: 20px; }
summary { cursor: pointer; color: var(--oe2-accent-soft); font-size: 13px; }
.add-form { margin-top: 12px; max-width: 520px; }
.row { display: grid; grid-template-columns: 140px 1fr; gap: 8px 12px; margin: 8px 0; align-items: center; font-size: 12px; }
.row label { color: var(--oe2-muted); font-size: 11px; text-transform: uppercase; }
.row input {
  background: var(--oe2-nav-sub-bg); border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color); padding: 5px 8px; border-radius: 2px; font-size: 12px;
}
.groups { display: flex; gap: 12px; }
.chk { display: flex; align-items: center; gap: 4px; font-size: 12px; }
</style>
