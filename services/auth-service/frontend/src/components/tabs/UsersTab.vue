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
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="u in users" :key="u.username">
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
          <td class="right">
            <button class="oe2-btn oe2-btn--danger" @click="deleteUser(u.username)" :title="t('delete', 'Supprimer')">
              <i class="fa-solid fa-trash"></i>
            </button>
          </td>
        </tr>
        <tr v-if="!users.length">
          <td colspan="5" class="loading">{{ t('users_empty', 'Aucun utilisateur') }}</td>
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
h2 { font-size: 14px; margin: 0 0 12px; font-weight: 400; }
.loading { color: var(--oe2-muted); text-align: center; padding: 20px; }
.table { width: 100%; border-collapse: collapse; font-size: 12px; }
.table th, .table td {
  padding: 6px 10px; text-align: left;
  border-bottom: 1px solid var(--oe2-border-subtle);
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
