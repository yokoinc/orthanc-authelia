<script setup>
import { ref, onMounted, computed } from 'vue'
import { api } from '../../api.js'
import { t } from '../../i18n.js'
import { useUiStore } from '../../stores/ui.js'

const ui = useUiStore()
const entries = ref([])
const loading = ref(true)
const filter = ref('')

// Every event carries an icon and a colour: in a log, the nature of an
// action should be seen before it is read. Security refusals stand out in
// red, deletions too.
const APPEARANCE = {
  'authelia.user.added':                   ['fa-user-plus', 'ok'],
  'authelia.user.updated':                 ['fa-user-pen', 'neutral'],
  'authelia.user.deleted':                 ['fa-user-minus', 'danger'],
  'authelia.password.changed':             ['fa-key', 'neutral'],
  'orthanc.config.updated':                ['fa-server', 'ok'],
  'orthanc.config.updated_pending_restart': ['fa-server', 'warning'],
  'orthanc.config.rolled_back':            ['fa-rotate-left', 'warning'],
  'orthanc.config.rollback_failed':        ['fa-triangle-exclamation', 'danger'],
  'backup.restored':                       ['fa-rotate-left', 'warning'],
  'network.public_url.changed':            ['fa-globe', 'warning'],
  'setup.admin.created':                   ['fa-shield-halved', 'ok'],
  'setup.finalized':                       ['fa-flag-checkered', 'ok'],
  'csrf.token':                            ['fa-ban', 'danger'],
  'csrf.origin':                           ['fa-ban', 'danger'],
}

function appearance(eventName) {
  return APPEARANCE[eventName] || ['fa-circle-info', 'neutral']
}

function readableDate(timestamp) {
  if (!timestamp) return '—'
  return new Date(timestamp * 1000).toLocaleString()
}

function readableDetails(details) {
  const entries = Object.entries(details || {})
  if (!entries.length) return ''
  return entries.map(([k, v]) => `${k} : ${v}`).join(' · ')
}

const filtered = computed(() => {
  const f = filter.value.trim().toLowerCase()
  if (!f) return entries.value
  return entries.value.filter((e) =>
    e.event.toLowerCase().includes(f) ||
    e.actor.toLowerCase().includes(f) ||
    readableDetails(e.details).toLowerCase().includes(f),
  )
})

async function load() {
  loading.value = true
  try {
    const data = await api('/console/api/admin/audit?limit=200')
    entries.value = data.entries
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="header-row">
      <h2>{{ t('audit_title', "Journal d'activité") }}</h2>
      <div class="actions">
        <input v-model="filter" :placeholder="t('audit_filter', 'Filtrer…')">
        <button class="oe2-btn oe2-btn--secondary" @click="load">
          <i class="fa-solid fa-rotate"></i> {{ t('refresh', 'Rafraîchir') }}
        </button>
      </div>
    </div>

    <p class="note">
      {{ t('audit_note', "Les 200 derniers événements : comptes, configuration Orthanc, sauvegardes restaurées et requêtes rejetées pour raison de sécurité.") }}
    </p>

    <div v-if="loading" class="loading">{{ t('loading', 'Chargement…') }}</div>

    <table v-else-if="filtered.length" class="table">
      <thead>
        <tr>
          <th>{{ t('audit_col_date', 'Date') }}</th>
          <th>{{ t('audit_col_event', 'Événement') }}</th>
          <th>{{ t('audit_col_actor', 'Auteur') }}</th>
          <th>{{ t('audit_col_details', 'Détails') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="e in filtered" :key="e.id">
          <td class="date">{{ readableDate(e.ts) }}</td>
          <td>
            <i :class="['fa-solid', appearance(e.event)[0], 'ic', 'ic--' + appearance(e.event)[1]]"></i>
            <span class="ev">{{ e.event }}</span>
          </td>
          <td>{{ e.actor }}</td>
          <td class="det">{{ readableDetails(e.details) }}</td>
        </tr>
      </tbody>
    </table>

    <div v-else class="loading">
      {{ filter ? t('audit_no_match', 'Aucun événement ne correspond') : t('audit_empty', 'Journal vide') }}
    </div>
  </div>
</template>

<style scoped>
.header-row { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
h2 { font-size: var(--oe2-fs-body); margin: 0 0 6px; font-weight: 400; }
.actions { display: flex; gap: 6px; align-items: center; }
.actions input {
  background: var(--oe2-nav-sub-bg);
  border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color);
  padding: 5px 8px; border-radius: 3px;
  font-family: var(--oe2-font-stack); font-size: var(--oe2-fs-small);
}
.note { color: var(--oe2-muted); font-size: var(--oe2-fs-small); margin: 0 0 12px; max-width: 80ch; }
.loading { color: var(--oe2-muted); text-align: center; padding: 20px; }
.table { width: 100%; border-collapse: collapse; font-size: var(--oe2-fs-small); }
.table th, .table td {
  padding: 5px 10px; text-align: left;
  border-bottom: 1px solid var(--oe2-separator);
}
.table th {
  color: var(--oe2-muted); text-transform: uppercase;
  letter-spacing: 0.5px; font-weight: 400; font-size: var(--oe2-fs-tiny);
}
.date { white-space: nowrap; color: var(--oe2-muted); }
.ev { font-family: var(--oe2-font-mono); font-size: var(--oe2-fs-tiny); }
.det { color: var(--oe2-muted); font-size: var(--oe2-fs-tiny); }
.ic { width: 16px; margin-right: 8px; text-align: center; }
.ic--ok { color: var(--oe2-success); }
.ic--danger { color: var(--oe2-danger); }
.ic--warning { color: var(--oe2-accent-orange); }
.ic--neutral { color: var(--oe2-muted); }
</style>
