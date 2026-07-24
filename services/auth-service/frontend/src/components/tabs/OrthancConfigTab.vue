<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../../api.js'
import { useUiStore } from '../../stores/ui.js'

const ui = useUiStore()
const fields = ref({})
const loading = ref(true)
const saving = ref(false)
const originalFields = ref({})

function detectType(val) {
  if (typeof val === 'boolean') return 'bool'
  if (typeof val === 'number') return 'number'
  return 'text'
}

function isModified(key) {
  return JSON.stringify(fields.value[key]) !== JSON.stringify(originalFields.value[key])
}

async function load() {
  loading.value = true
  try {
    const data = await api('/api/admin/orthanc/config')
    fields.value = { ...data.editable }
    originalFields.value = JSON.parse(JSON.stringify(data.editable))
  } catch (e) {
    ui.notify('Erreur chargement config : ' + e.message, 'err')
  } finally {
    loading.value = false
  }
}

async function save() {
  const changes = {}
  for (const key in fields.value) {
    if (isModified(key)) changes[key] = fields.value[key]
  }
  if (!Object.keys(changes).length) {
    ui.notify('Aucun changement a appliquer', 'ok')
    return
  }
  saving.value = true
  try {
    const r = await api('/api/admin/orthanc/config', {
      method: 'PATCH', body: { changes },
    })
    ui.notify(`Applique. Backup : ${r.backup}`, 'ok')
    originalFields.value = JSON.parse(JSON.stringify(fields.value))
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h2>Configuration Orthanc</h2>
    <p class="note">
      Édite directement <code>orthanc.json</code> (bind-mount). L'application
      se fait par <code>POST /tools/reset</code>, sans process restart.
      Backup <code>.bak</code> auto avant écriture.
    </p>

    <div v-if="loading" class="loading">Chargement…</div>

    <div v-else class="fields">
      <div v-for="(val, key) in fields" :key="key" class="row" :class="{ 'row--modified': isModified(key) }">
        <label>{{ key }}</label>
        <select v-if="detectType(val) === 'bool'" v-model="fields[key]">
          <option :value="true">true</option>
          <option :value="false">false</option>
        </select>
        <input v-else-if="detectType(val) === 'number'" v-model.number="fields[key]" type="number">
        <input v-else v-model="fields[key]" type="text">
        <span class="flag">{{ isModified(key) ? '● modifié' : '' }}</span>
      </div>

      <div class="toolbar">
        <button class="btn btn--primary" :disabled="saving" @click="save">
          <i class="fa-solid fa-check"></i>
          {{ saving ? 'Application…' : 'Appliquer & recharger Orthanc' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
h2 { font-size: 14px; margin: 0 0 12px; font-weight: 400; }
.note { color: var(--oe2-muted); font-size: 12px; margin: 0 0 16px; }
.note code {
  background: var(--oe2-nav-sub-bg); padding: 1px 5px;
  border-radius: 2px; font-size: 11px;
}
.loading { color: var(--oe2-muted); text-align: center; padding: 20px; }
.fields { max-width: 640px; }
.row {
  display: grid;
  grid-template-columns: 260px 1fr 80px;
  gap: 8px 12px;
  padding: 4px 8px;
  align-items: center;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}
.row--modified { background: rgba(209,155,61,0.05); }
.row label {
  font-family: var(--oe2-font-mono);
  font-size: 11px;
}
.row input, .row select {
  background: var(--oe2-nav-sub-bg);
  border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color);
  padding: 4px 8px;
  border-radius: 2px;
  font-size: 12px;
  font-family: var(--oe2-font-mono);
}
.flag { font-size: 10px; color: var(--oe2-accent-orange); }
.toolbar { margin-top: 16px; text-align: right; }
.btn {
  padding: 8px 16px; border: none; border-radius: 3px; cursor: pointer;
  font-size: 13px;
}
.btn--primary { background: var(--oe2-accent); color: white; }
.btn--primary:hover:not(:disabled) { background: var(--oe2-accent-soft); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
