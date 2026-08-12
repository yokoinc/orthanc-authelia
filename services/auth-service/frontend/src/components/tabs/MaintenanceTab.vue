<script setup>
import { ref, onMounted, computed } from 'vue'
import { api } from '../../api.js'
import { t } from '../../i18n.js'
import { useUiStore } from '../../stores/ui.js'

const ui = useUiStore()

// --- Public address --------------------------------------------------------
const url = ref('')
const initialUrl = ref('')
const urlEditable = ref(false)
const savingUrl = ref(false)

const urlChanged = computed(() => url.value !== initialUrl.value && url.value.length > 7)

// --- Interface language ----------------------------------------------------
const LANGUAGES = [
  { id: 'fr', label: 'Français' },
  { id: 'en', label: 'English' },
]
const language = ref('')
const initialLanguage = ref('')
const savingLanguage = ref(false)

async function saveLanguage() {
  savingLanguage.value = true
  try {
    await api('/console/api/admin/language', {
      method: 'PUT', body: { language: language.value },
    })
    initialLanguage.value = language.value
    // Labels are injected into the page on load: without a reload the
    // interface would stay in the previous language.
    window.location.reload()
  } catch (e) {
    ui.notify(e.message, 'err')
    savingLanguage.value = false
  }
}

async function loadPreferences() {
  try {
    const d = await api('/console/api/admin/preferences')
    language.value = d.language
    initialLanguage.value = d.language
  } catch (e) {
    ui.notify(e.message, 'err')
  }
}

async function saveViewer() {
  savingViewer.value = true
  try {
    await api('/console/api/admin/sharing', {
      method: 'PUT', body: { default_viewer: viewer.value },
    })
    initialViewer.value = viewer.value
    ui.notify(t('sharing_saved', 'Viewer par défaut enregistré.'), 'ok')
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    savingViewer.value = false
  }
}

// --- Backups ---------------------------------------------------------------
const backups = ref([])
const loading = ref(true)
const backingUp = ref(false)

// Copies were only created in reaction to a panel write: there was no way
// to take one before a risky operation.
async function createBackup() {
  backingUp.value = true
  try {
    const r = await api('/console/api/admin/backups', { method: 'POST' })
    ui.notify(t('backup_created', '{count} fichier(s) sauvegardé(s).', { count: r.created.length }), 'ok')
    load()
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    backingUp.value = false
  }
}

function readableDate(timestamp) {
  return new Date(timestamp * 1000).toLocaleString()
}

function readableSize(bytes) {
  if (bytes < 1024) return `${bytes} o`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} ko`
  return `${(bytes / 1024 / 1024).toFixed(1)} Mo`
}

async function load() {
  loading.value = true
  try {
    const network = await api('/console/api/admin/network')
    url.value = network.public_url || ''
    initialUrl.value = url.value
    urlEditable.value = network.editable
  } catch (e) {
    ui.notify(e.message, 'err')
  }
  try {
    const data = await api('/console/api/admin/backups')
    backups.value = data.backups
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    loading.value = false
  }
}

async function saveUrl() {
  if (!urlChanged.value) return
  if (!confirm(t('network_confirm', "Changer l'adresse publique impose de redémarrer la pile et de se reconnecter sur la nouvelle adresse. Continuer ?"))) return
  savingUrl.value = true
  try {
    await api('/console/api/admin/network', {
      method: 'POST',
      body: { public_url: url.value },
    })
    initialUrl.value = url.value
    ui.notify(t('network_saved', 'Adresse enregistrée. Redémarrer la pile pour appliquer, puis se reconnecter sur la nouvelle adresse.'), 'ok')
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    savingUrl.value = false
  }
}

async function restore(name) {
  if (!confirm(t('backup_confirm', 'Restaurer « {name} » ? Le fichier actuel sera sauvegardé avant d\'être remplacé.', { name: name }))) return
  try {
    await api(`/console/api/admin/backups/restore?backup_name=${encodeURIComponent(name)}`, {
      method: 'POST',
    })
    ui.notify(t('backup_restored', 'Sauvegarde restaurée.'), 'ok')
    load()
  } catch (e) {
    ui.notify(e.message, 'err')
  }
}

onMounted(() => { load(); loadPreferences() })
</script>

<template>
  <div>
    <h2>{{ t('network_title', 'Adresse publique') }}</h2>
    <p class="note">
      {{ t('network_note', "Adresse par laquelle les navigateurs joignent le PACS, port compris. Elle sert aux redirections, aux cookies de session et au certificat.") }}
    </p>

    <div class="ligne">
      <input
        v-model="url" type="url" :disabled="!urlModifiable"
        placeholder="https://pacs.exemple.fr:30443"
      >
      <button
        class="oe2-btn oe2-btn--primary"
        :disabled="!urlModifiee || enregistrementUrl || !urlModifiable"
        @click="enregistrerUrl"
      >
        {{ enregistrementUrl ? t('saving', 'Enregistrement…') : t('save', 'Enregistrer') }}
      </button>
    </div>
    <div v-if="!urlModifiable" class="avert">
      {{ t('network_readonly', "Le fichier .env n'est pas accessible : l'adresse ne peut pas être modifiée depuis le panel.") }}
    </div>

    <h2 class="espace">{{ t('langue_title', 'Langue de l\'interface') }}</h2>
    <p class="note">
      {{ t('langue_note', "Langue du panel et des pages de partage. Le changement recharge la page.") }}
    </p>

    <div class="ligne">
      <select v-model="langue">
        <option v-for="l in LANGUES" :key="l.id" :value="l.id">{{ l.libelle }}</option>
      </select>
      <button
        class="oe2-btn oe2-btn--primary"
        :disabled="langue === langueInitiale || enregistrementLangue"
        @click="enregistrerLangue"
      >
        {{ enregistrementLangue ? t('saving', 'Enregistrement…') : t('save', 'Enregistrer') }}
      </button>
    </div>

    <div class="entete espace">
      <h2>{{ t('backups_title', 'Sauvegardes') }}</h2>
      <button class="oe2-btn oe2-btn--primary" :disabled="sauvegardeEnCours" @click="sauvegarder">
        <i class="fa-solid fa-floppy-disk"></i>
        {{ sauvegardeEnCours ? t('backup_creating', 'Sauvegarde…') : t('backup_now', 'Sauvegarder maintenant') }}
      </button>
    </div>
    <p class="note">
      {{ t('backups_note', "Créées automatiquement avant chaque écriture de la configuration Orthanc ou de la liste des utilisateurs. Les dix dernières sont conservées.") }}
    </p>

    <div v-if="chargement" class="loading">{{ t('loading', 'Chargement…') }}</div>

    <table v-else-if="sauvegardes.length" class="table">
      <thead>
        <tr>
          <th>{{ t('backups_col_date', 'Date') }}</th>
          <th>{{ t('backups_col_target', 'Fichier') }}</th>
          <th>{{ t('backups_col_size', 'Taille') }}</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in sauvegardes" :key="s.name">
          <td>{{ dateLisible(s.modified) }}</td>
          <td>
            <span class="cible">{{ s.target === 'orthanc' ? 'orthanc.json' : 'users_database.yml' }}</span>
          </td>
          <td>{{ tailleLisible(s.size) }}</td>
          <td class="right">
            <button class="oe2-btn oe2-btn--secondary" @click="restaurer(s.name)">
              <i class="fa-solid fa-rotate-left"></i> {{ t('restore', 'Restaurer') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-else class="loading">{{ t('backups_empty', 'Aucune sauvegarde') }}</div>
  </div>
</template>

<style scoped>
h2 { font-size: var(--oe2-fs-body); margin: 0 0 6px; font-weight: 400; }
.espace { margin-top: 28px; }
.entete { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.note { color: var(--oe2-muted); font-size: var(--oe2-fs-small); margin: 0 0 12px; max-width: 70ch; }
.ligne { display: flex; gap: 8px; align-items: center; }
.ligne select {
    /* Same footprint as the neighbouring text fields, without stretching
       toute la ligne : la liste est courte. */
    min-width: 220px;
}
.ligne input {
  flex: 1; max-width: 420px;
  background: var(--oe2-nav-sub-bg);
  border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color);
  padding: 6px 10px; border-radius: 3px;
  font-family: var(--oe2-font-stack); font-size: var(--oe2-fs-small);
}
.ligne input:disabled { opacity: 0.5; }
.avert {
  margin-top: 8px; font-size: var(--oe2-fs-tiny); color: #e8c98a;
  border-left: 3px solid var(--oe2-accent-orange);
  padding: 6px 10px; background: rgba(209, 155, 61, 0.12);
}
.loading { color: var(--oe2-muted); text-align: center; padding: 20px; }
.table { width: 100%; border-collapse: collapse; font-size: var(--oe2-fs-small); }
.table th, .table td {
  padding: 6px 10px; text-align: left;
  border-bottom: 1px solid var(--oe2-separator);
}
.table th {
  color: var(--oe2-muted); text-transform: uppercase;
  letter-spacing: 0.5px; font-weight: 400; font-size: var(--oe2-fs-tiny);
}
.right { text-align: right; }
.cible { font-family: var(--oe2-font-mono); font-size: var(--oe2-fs-tiny); }
</style>
