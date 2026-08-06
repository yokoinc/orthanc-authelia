<script setup>
import { ref, onMounted, computed } from 'vue'
import { api } from '../../api.js'
import { t } from '../../i18n.js'
import { useUiStore } from '../../stores/ui.js'

const ui = useUiStore()
const entrees = ref([])
const chargement = ref(true)
const filtre = ref('')

// Chaque evenement porte une icone et une couleur : sur un journal, la nature
// de l'action doit se voir avant d'etre lue. Les refus de securite ressortent
// en rouge, les suppressions aussi.
const APPARENCE = {
  'authelia.user.added':                   ['fa-user-plus', 'ok'],
  'authelia.user.updated':                 ['fa-user-pen', 'neutre'],
  'authelia.user.deleted':                 ['fa-user-minus', 'danger'],
  'authelia.password.changed':             ['fa-key', 'neutre'],
  'orthanc.config.updated':                ['fa-server', 'ok'],
  'orthanc.config.updated_pending_restart': ['fa-server', 'attention'],
  'orthanc.config.rolled_back':            ['fa-rotate-left', 'attention'],
  'orthanc.config.rollback_failed':        ['fa-triangle-exclamation', 'danger'],
  'backup.restored':                       ['fa-rotate-left', 'attention'],
  'network.public_url.changed':            ['fa-globe', 'attention'],
  'setup.admin.created':                   ['fa-shield-halved', 'ok'],
  'setup.finalized':                       ['fa-flag-checkered', 'ok'],
  'csrf.token':                            ['fa-ban', 'danger'],
  'csrf.origin':                           ['fa-ban', 'danger'],
}

function apparence(evenement) {
  return APPARENCE[evenement] || ['fa-circle-info', 'neutre']
}

function dateLisible(horodatage) {
  if (!horodatage) return '—'
  return new Date(horodatage * 1000).toLocaleString()
}

function detailsLisibles(details) {
  const entrees = Object.entries(details || {})
  if (!entrees.length) return ''
  return entrees.map(([k, v]) => `${k} : ${v}`).join(' · ')
}

const filtrees = computed(() => {
  const f = filtre.value.trim().toLowerCase()
  if (!f) return entrees.value
  return entrees.value.filter((e) =>
    e.event.toLowerCase().includes(f) ||
    e.actor.toLowerCase().includes(f) ||
    detailsLisibles(e.details).toLowerCase().includes(f),
  )
})

async function charger() {
  chargement.value = true
  try {
    const data = await api('/console/api/admin/audit?limit=200')
    entrees.value = data.entries
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    chargement.value = false
  }
}

onMounted(charger)
</script>

<template>
  <div>
    <div class="entete">
      <h2>{{ t('audit_title', "Journal d'activité") }}</h2>
      <div class="actions">
        <input v-model="filtre" :placeholder="t('audit_filter', 'Filtrer…')">
        <button class="oe2-btn oe2-btn--secondary" @click="charger">
          <i class="fa-solid fa-rotate"></i> {{ t('refresh', 'Rafraîchir') }}
        </button>
      </div>
    </div>

    <p class="note">
      {{ t('audit_note', "Les 200 derniers événements : comptes, configuration Orthanc, sauvegardes restaurées et requêtes rejetées pour raison de sécurité.") }}
    </p>

    <div v-if="chargement" class="loading">{{ t('loading', 'Chargement…') }}</div>

    <table v-else-if="filtrees.length" class="table">
      <thead>
        <tr>
          <th>{{ t('audit_col_date', 'Date') }}</th>
          <th>{{ t('audit_col_event', 'Événement') }}</th>
          <th>{{ t('audit_col_actor', 'Auteur') }}</th>
          <th>{{ t('audit_col_details', 'Détails') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="e in filtrees" :key="e.id">
          <td class="date">{{ dateLisible(e.ts) }}</td>
          <td>
            <i :class="['fa-solid', apparence(e.event)[0], 'ic', 'ic--' + apparence(e.event)[1]]"></i>
            <span class="ev">{{ e.event }}</span>
          </td>
          <td>{{ e.actor }}</td>
          <td class="det">{{ detailsLisibles(e.details) }}</td>
        </tr>
      </tbody>
    </table>

    <div v-else class="loading">
      {{ filtre ? t('audit_no_match', 'Aucun événement ne correspond') : t('audit_empty', 'Journal vide') }}
    </div>
  </div>
</template>

<style scoped>
.entete { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
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
.ic--attention { color: var(--oe2-accent-orange); }
.ic--neutre { color: var(--oe2-muted); }
</style>
