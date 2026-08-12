<script setup>
import { ref, reactive, onMounted } from 'vue'
import { api } from '../../api.js'
import { t } from '../../i18n.js'
import { useUiStore } from '../../stores/ui.js'

const ui = useUiStore()
const devices = ref([])
const loading = ref(true)
const formOpen = ref(false)
const saving = ref(false)
const testing = ref('')
const results = reactive({})

const draft = reactive({ name: '', aet: '', host: '', port: 104 })

function isValid() {
  return draft.name.trim() && draft.aet.trim() &&
    draft.aet.length <= 16 && draft.host.trim() &&
    draft.port >= 1 && draft.port <= 65535
}

async function load() {
  loading.value = true
  try {
    const data = await api('/console/api/admin/modalities')
    devices.value = data.modalities
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!isValid()) return
  saving.value = true
  try {
    await api(`/console/api/admin/modalities/${encodeURIComponent(draft.name.trim())}`, {
      method: 'PUT',
      body: { aet: draft.aet.trim(), host: draft.host.trim(), port: draft.port },
    })
    ui.notify(t('modality_saved', 'Équipement {name} enregistré.', { name: draft.name.trim() }), 'ok')
    Object.assign(draft, { name: '', aet: '', host: '', port: 104 })
    formOpen.value = false
    load()
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    saving.value = false
  }
}

async function remove(name) {
  if (!confirm(t('modality_delete_confirm', "Supprimer l'équipement « {name} » ?", { name: name }))) return
  try {
    await api(`/console/api/admin/modalities/${encodeURIComponent(name)}`, { method: 'DELETE' })
    ui.notify(t('modality_deleted', '{name} a été supprimé.', { name: name }), 'ok')
    delete results[name]
    load()
  } catch (e) {
    ui.notify(e.message, 'err')
  }
}

// A declared device is not necessarily reachable: wrong address, closed
// port, refused AE title. The test avoids discovering the problem on the day
// of a transfer.
async function test(name) {
  testing.value = name
  try {
    const r = await api(`/console/api/admin/modalities/${encodeURIComponent(name)}/echo`, {
      method: 'POST',
    })
    results[name] = r.reachable ? 'ok' : 'ko'
    ui.notify(
      r.reachable
        ? t('modality_echo_ok', '{name} répond.', { name: name })
        : t('modality_echo_ko', '{name} ne répond pas.', { name: name }),
      r.reachable ? 'ok' : 'err',
    )
  } catch (e) {
    results[name] = 'ko'
    ui.notify(e.message, 'err')
  } finally {
    testing.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h2>{{ t('modalities_title', 'Équipements DICOM') }}</h2>
    <p class="note">
      {{ t('modalities_note', "Appareils avec lesquels ce serveur échange des examens : scanners, IRM, stations de post-traitement. Les déclarations prennent effet immédiatement, sans redémarrage.") }}
    </p>

    <div v-if="chargement" class="loading">{{ t('loading', 'Chargement…') }}</div>

    <table v-else-if="equipements.length" class="table">
      <thead>
        <tr>
          <th>{{ t('modality_col_name', 'Nom') }}</th>
          <th>{{ t('modality_col_aet', 'Titre AE') }}</th>
          <th>{{ t('modality_col_host', 'Adresse') }}</th>
          <th>{{ t('modality_col_port', 'Port') }}</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="m in equipements" :key="m.name">
          <td>
            <strong>{{ m.name }}</strong>
            <span v-if="resultats[m.name]" :class="['pastille', 'pastille--' + resultats[m.name]]">
              {{ resultats[m.name] === 'ok' ? t('modality_reachable', 'joignable') : t('modality_unreachable', 'injoignable') }}
            </span>
          </td>
          <td class="mono">{{ m.aet }}</td>
          <td class="mono">{{ m.host }}</td>
          <td class="mono">{{ m.port }}</td>
          <td class="right">
            <button
              class="oe2-btn oe2-btn--secondary"
              :disabled="testEnCours === m.name"
              :title="t('modality_test', 'Tester la connexion')"
              @click="tester(m.name)"
            >
              <i class="fa-solid fa-tower-broadcast"></i>
              {{ testEnCours === m.name ? t('modality_testing', 'Test…') : t('modality_test', 'Tester') }}
            </button>
            <button class="oe2-btn oe2-btn--danger" :title="t('delete', 'Supprimer')" @click="supprimer(m.name)">
              <i class="fa-solid fa-trash"></i>
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-else class="loading">{{ t('modalities_empty', 'Aucun équipement déclaré') }}</div>

    <details :open="formulaireOuvert" @toggle="formulaireOuvert = $event.target.open">
      <summary>{{ t('modality_add', '+ Déclarer un équipement') }}</summary>
      <form class="form" @submit.prevent="enregistrer">
        <div class="champ">
          <label>{{ t('modality_col_name', 'Nom') }}</label>
          <input v-model="nouveau.name" required maxlength="64" placeholder="SCANNER-1">
          <span class="aide">{{ t('modality_name_help', 'Identifiant libre, utilisé dans Orthanc') }}</span>
        </div>
        <div class="champ">
          <label>{{ t('modality_col_aet', 'Titre AE') }}</label>
          <input v-model="nouveau.aet" required maxlength="16" placeholder="SCANNER1">
          <span class="aide">{{ t('modality_aet_help', '16 caractères maximum, imposé par la norme DICOM') }}</span>
        </div>
        <div class="champ">
          <label>{{ t('modality_col_host', 'Adresse') }}</label>
          <input v-model="nouveau.host" required placeholder="192.168.1.50">
        </div>
        <div class="champ">
          <label>{{ t('modality_col_port', 'Port') }}</label>
          <input v-model.number="nouveau.port" type="number" min="1" max="65535">
          <span class="aide">{{ t('modality_port_help', '104 par convention') }}</span>
        </div>
        <div class="champ">
          <button type="submit" class="oe2-btn oe2-btn--primary" :disabled="!valide() || envoi">
            {{ envoi ? t('saving', 'Enregistrement…') : t('save', 'Enregistrer') }}
          </button>
        </div>
      </form>
    </details>
  </div>
</template>

<style scoped>
h2 { font-size: var(--oe2-fs-body); margin: 0 0 6px; font-weight: 400; }
.note { color: var(--oe2-muted); font-size: var(--oe2-fs-small); margin: 0 0 14px; max-width: 80ch; }
.loading { color: var(--oe2-muted); text-align: center; padding: 20px; }
.table { width: 100%; border-collapse: collapse; font-size: var(--oe2-fs-small); margin-bottom: 12px; }
.table th, .table td {
  padding: 6px 10px; text-align: left;
  border-bottom: 1px solid var(--oe2-separator);
}
.table th {
  color: var(--oe2-muted); text-transform: uppercase;
  letter-spacing: 0.5px; font-weight: 400; font-size: var(--oe2-fs-tiny);
}
.right { text-align: right; white-space: nowrap; }
.mono { font-family: var(--oe2-font-mono); font-size: var(--oe2-fs-tiny); }
.pastille { margin-left: 8px; font-size: var(--oe2-fs-micro); padding: 1px 6px; border-radius: 2px; }
.pastille--ok { background: rgba(40,167,69,0.2); color: #b6f0c0; }
.pastille--ko { background: rgba(220,53,69,0.2); color: #ffb0b0; }
summary { cursor: pointer; font-size: var(--oe2-fs-small); color: var(--oe2-link); margin-top: 6px; }
.form {
  display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap;
  padding: 12px 8px; margin-top: 8px;
  background: var(--oe2-nav-sub-bg); border-radius: 3px;
}
.champ { display: flex; flex-direction: column; gap: 3px; }
.champ label { color: var(--oe2-muted); font-size: var(--oe2-fs-tiny); }
.champ input {
  background: var(--oe2-nav-bg);
  border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color);
  padding: 5px 8px; border-radius: 3px;
  font-family: var(--oe2-font-stack); font-size: var(--oe2-fs-small);
}
.aide { font-size: var(--oe2-fs-micro); color: var(--oe2-muted); }
</style>
