<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { api } from '../../api.js'
import { t } from '../../i18n.js'
import { useUiStore } from '../../stores/ui.js'

const ui = useUiStore()
const users = ref([])

// The last active administrator must not be deleted, removed from their
// group, or disabled: the stack would be left with nobody to administer it,
// and the only way out would be editing users_database.yml by hand. The
// server already refuses these operations, but the interface offered them
// anyway -- the refusal only surfaced afterwards.
//
// Changing the password stays available: without it, that account could
// never change its own again.
const activeAdmins = computed(
  () => users.value.filter((u) => !u.disabled && (u.groups || []).includes('admin')),
)

function isLastAdmin(u) {
  return activeAdmins.value.length === 1 && activeAdmins.value[0].username === u.username
}
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

// Editing an account: same expanding-row approach as the password, so as
// not to leave the table.
const editingFor = ref('')
const editing = reactive({ displayname: '', email: '', groups: [], disabled: false })
const savingEdit = ref(false)

function openEdit(u) {
  if (editingFor.value === u.username) {
    editingFor.value = ''
    return
  }
  editingFor.value = u.username
  passwordFor.value = ''
  Object.assign(editing, {
    displayname: u.displayname || '',
    email: u.email || '',
    groups: [...(u.groups || [])],
    disabled: !!u.disabled,
  })
}

function toggleEditGroup(g) {
  const i = editing.groups.indexOf(g)
  if (i >= 0) editing.groups.splice(i, 1)
  else editing.groups.push(g)
}

async function saveEdit() {
  savingEdit.value = true
  try {
    await api(`/console/api/admin/users/${encodeURIComponent(editingFor.value)}`, {
      method: 'PATCH',
      body: {
        displayname: editing.displayname,
        email: editing.email,
        groups: editing.groups,
        disabled: editing.disabled,
      },
    })
    ui.notify(t('users_updated', '{username} a été modifié.', { username: editingFor.value }), 'ok')
    editingFor.value = ''
    load()
  } catch (e) {
    // The server refuses to leave the stack without an active
    // administrator: the message explains what blocks, do not hide it.
    ui.notify(e.message, 'err')
  } finally {
    savingEdit.value = false
  }
}

// Password change: inline input rather than a prompt(), which would show
// the password in clear and allows no validation.
const passwordFor = ref('')
const newPassword = ref('')
const savingPassword = ref(false)

function openPassword(username) {
  passwordFor.value = passwordFor.value === username ? '' : username
  newPassword.value = ''
}

async function savePassword() {
  if (newPassword.value.length < 12) return
  savingPassword.value = true
  try {
    await api(`/console/api/admin/users/${encodeURIComponent(passwordFor.value)}/password`, {
      method: 'PATCH',
      body: { new_password: newPassword.value },
    })
    ui.notify(t('users_password_changed', 'Mot de passe modifié pour {username}.', { username: passwordFor.value }), 'ok')
    passwordFor.value = ''
    newPassword.value = ''
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    savingPassword.value = false
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
            <span v-if="estDernierAdmin(u)" class="protege">
              <i class="fa-solid fa-lock"></i> {{ t('users_protected', 'protégé') }}
            </span>
          </td>
          <td class="right">
            <button
              class="oe2-btn oe2-btn--secondary"
              :disabled="estDernierAdmin(u)"
              :title="estDernierAdmin(u)
                ? t('users_last_admin_locked', 'Dernier administrateur actif : ni modification ni suppression possibles. Créez un second administrateur pour débloquer.')
                : t('users_edit', 'Modifier')"
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
            <button
              class="oe2-btn oe2-btn--danger"
              :disabled="estDernierAdmin(u)"
              :title="estDernierAdmin(u)
                ? t('users_last_admin_locked', 'Dernier administrateur actif : ni modification ni suppression possibles. Créez un second administrateur pour débloquer.')
                : t('delete', 'Supprimer')"
              @click="deleteUser(u.username)"
            >
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
.etat { font-size: var(--oe2-fs-tiny); }
.etat--on { color: var(--oe2-success); }
.etat--off { color: var(--oe2-muted); }
.protege { margin-left: 8px; font-size: var(--oe2-fs-micro); color: var(--oe2-accent-soft); }
.edit-ligne {
  background: var(--oe2-nav-sub-bg);
  display: flex; align-items: flex-end; gap: 14px; flex-wrap: wrap;
}
.edit-ligne .champ { display: flex; flex-direction: column; gap: 3px; }
.edit-ligne label { color: var(--oe2-muted); font-size: var(--oe2-fs-tiny); }
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
.pwd-ligne label { color: var(--oe2-muted); font-size: var(--oe2-fs-tiny); }
.pwd-ligne input {
  background: var(--oe2-nav-bg);
  border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color);
  padding: 5px 8px; border-radius: 3px;
  font-family: var(--oe2-font-stack); font-size: var(--oe2-fs-small);
}
h2 { font-size: var(--oe2-fs-body); margin: 0 0 12px; font-weight: 400; }
.loading { color: var(--oe2-muted); text-align: center; padding: 20px; }
.table { width: 100%; border-collapse: collapse; font-size: var(--oe2-fs-small); }
.table th, .table td {
  padding: 6px 10px; text-align: left;
  /* Light blue like Orthanc's tables, see --oe2-table-border. */
  border-bottom: 1px solid var(--oe2-table-border);
}
.table th {
  color: var(--oe2-muted); text-transform: uppercase;
  letter-spacing: 0.5px; font-weight: 400; font-size: var(--oe2-fs-tiny);
}
.right { text-align: right; }
.badge {
  display: inline-block; padding: 2px 6px; border-radius: 2px;
  font-size: var(--oe2-fs-micro); text-transform: uppercase; margin-right: 4px;
}
.badge--admin { background: var(--oe2-accent-orange); color: white; }
.badge--doctor { background: var(--oe2-label-bg); color: white; }
details { margin-top: 20px; }
summary { cursor: pointer; color: var(--oe2-accent-soft); font-size: var(--oe2-fs-medium); }
.add-form { margin-top: 12px; max-width: 520px; }
.row { display: grid; grid-template-columns: 140px 1fr; gap: 8px 12px; margin: 8px 0; align-items: center; font-size: var(--oe2-fs-small); }
.row label { color: var(--oe2-muted); font-size: var(--oe2-fs-tiny); text-transform: uppercase; }
.row input {
  background: var(--oe2-nav-sub-bg); border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color); padding: 5px 8px; border-radius: 2px; font-size: var(--oe2-fs-small);
}
.groups { display: flex; gap: 12px; }
.chk { display: flex; align-items: center; gap: 4px; font-size: var(--oe2-fs-small); }
</style>
