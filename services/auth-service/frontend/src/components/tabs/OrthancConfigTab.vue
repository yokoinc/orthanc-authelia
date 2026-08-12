<script setup>
import { ref, onMounted, computed } from 'vue'
import { api } from '../../api.js'
import { t } from '../../i18n.js'
import { useUiStore } from '../../stores/ui.js'
import { GROUPS } from '../../orthanc_fields.js'

const ui = useUiStore()
const fields = ref({})
const originalFields = ref({})
const loading = ref(true)
const saving = ref(false)
const restarting = ref(false)
// Becomes true as soon as a change is saved: it stays ineffective until
// Orthanc restarts, and nothing else would show it -- the panel would keep
// displaying the new value.
const restartRequired = ref(false)
const replies = ref({})

const meta = ref({})

// The type comes from the server, which takes it from its list of allowed
// settings. Deriving it from the value only worked for settings present in
// orthanc.json: the others are null and fell back to a text field, although
// they are often booleans.
function detectType(key) {
  const t = meta.value[key]?.type
  if (t === 'bool') return 'bool'
  if (t === 'int') return 'number'
  if (t === 'list') return 'list'
  return 'text'
}

// Setting absent from the file: Orthanc applies its default. Saying so
// avoids suggesting an empty setting.
function isDefault(key) {
  return meta.value[key]?.present === false
}

// The default value, formatted for display. Two settings are absent from
// Orthanc's reference file: we then show nothing rather than put forward an
// invented value.
function defaultValue(key) {
  const d = meta.value[key]?.default
  if (d === undefined || d === null) return ''
  if (typeof d === 'boolean') return d ? t('yes', 'Oui') : t('no', 'Non')
  if (Array.isArray(d)) return d.length ? d.join(', ') : '—'
  if (d === '') return '—'
  return String(d)
}

function isModified(key) {
  return JSON.stringify(fields.value[key]) !== JSON.stringify(originalFields.value[key])
}

// A setting's label and help text, or its technical name otherwise.
function label(key) {
  for (const g of GROUPS) {
    if (g.fields[key]) return g.fields[key][0]
  }
  return key
}
function help(key) {
  for (const g of GROUPS) {
    if (g.fields[key]) return g.fields[key][1]
  }
  return ''
}

// Settings the description does not cover stay visible in an "Other"
// group: a misfiled field beats an invisible one when the server-side list
// grows.
const visibleGroups = computed(() => {
  const known = new Set()
  const result = []

  for (const g of GROUPS) {
    const keys = Object.keys(g.fields).filter((c) => c in fields.value)
    keys.forEach((c) => known.add(c))
    if (keys.length) result.push({ ...g, keys })
  }

  const remaining = Object.keys(fields.value).filter((c) => !known.has(c))
  if (remaining.length) {
    result.push({
      id: 'autres',
      title: t('orthanc_group_other', 'Autres'),
      icon: 'fa-ellipsis',
      keys: remaining,
    })
  }
  return result
})

// Allowed values and bounds, as the server declares them. Reusing them
// rather than restating them keeps interface and validation from drifting
// apart: the server is what refuses, the interface only announces it
// earlier.
function allowedValues(key) {
  return meta.value[key]?.choices || null
}
function minBound(key) {
  return meta.value[key]?.min ?? null
}
function maxBound(key) {
  return meta.value[key]?.max ?? null
}

const modifiedCount = computed(
  () => Object.keys(fields.value).filter(isModified).length,
)

// Lists are edited as text, one value per line.
function listToText(v) {
  return Array.isArray(v) ? v.join('\n') : ''
}
function textToList(key, text) {
  fields.value[key] = text.split('\n').map((l) => l.trim()).filter(Boolean)
}

async function load() {
  loading.value = true
  try {
    const data = await api('/console/api/admin/orthanc/config')
    fields.value = { ...data.editable }
    meta.value = data.fields || {}
    originalFields.value = JSON.parse(JSON.stringify(data.editable))
  } catch (e) {
    ui.notify(t('orthanc_load_error', 'Erreur au chargement de la configuration : {detail}', { detail: e.message }), 'err')
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
    ui.notify(t('orthanc_no_change', 'Aucun changement à appliquer.'), 'ok')
    return
  }
  saving.value = true
  try {
    const r = await api('/console/api/admin/orthanc/config', {
      method: 'PATCH', body: { changes },
    })
    ui.notify(r.message || t('orthanc_applied', 'Enregistré. Sauvegarde : {backup}', { backup: r.backup }), 'ok')
    originalFields.value = JSON.parse(JSON.stringify(fields.value))
    restartRequired.value = true
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    saving.value = false
  }
}

async function restart() {
  if (!confirm(t(
    'orthanc_restart_confirm',
    "Redémarrer Orthanc ? Les consultations et transferts en cours seront interrompus pendant une trentaine de secondes.",
  ))) return
  restarting.value = true
  try {
    const r = await api('/console/api/admin/orthanc/restart', { method: 'POST' })
    ui.notify(r.message || t('orthanc_restarted', 'Orthanc a redémarré.'), 'ok')
    restartRequired.value = false
    await load()
  } catch (e) {
    ui.notify(e.message, 'err')
  } finally {
    restarting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h2>{{ t('orthanc_title', 'Configuration Orthanc') }}</h2>
    <p class="note">
      {{ t('orthanc_note', "Modifie directement orthanc.json. Une sauvegarde est créée avant chaque écriture. Le redémarrage du conteneur Orthanc est nécessaire pour que les changements prennent effet.") }}
    </p>

    <div v-if="loading" class="loading">{{ t('loading', 'Chargement…') }}</div>

    <div v-else>
      <section v-for="g in visibleGroups" :key="g.id" class="group">
        <h3><i :class="['fa-solid', g.icon]"></i> {{ g.title }}</h3>

        <div
          v-for="key in g.keys" :key="key"
          class="row" :class="{ 'row--modified': isModified(key) }"
        >
          <div class="intitule">
            <label :for="'c-' + key">{{ label(key) }}</label>
            <span class="tech">
              {{ key }}
              <em v-if="isDefault(key) && defaultValue(key)" class="defaut">
                {{ t('orthanc_default_is', '· par défaut : {value}', { value: defaultValue(key) }) }}
              </em>
              <em v-else-if="isDefault(key)" class="defaut">{{ t('orthanc_default', '· valeur par défaut d\'Orthanc') }}</em>
            </span>
            <span v-if="help(key)" class="hint">{{ help(key) }}</span>
            <span v-if="minBound(key) !== null" class="hint">
              {{ t('orthanc_range', 'Entre {min} et {max}.', { min: minBound(key), max: maxBound(key) }) }}
            </span>
          </div>

          <div class="value">
            <select v-if="detectType(key) === 'bool'" :id="'c-' + key" v-model="fields[key]">
              <!-- A setting absent from the file is null: without this
                   option the list had nothing to select and showed up empty.
                   Choosing it amounts to writing nothing, hence letting
                   Orthanc apply its default value. -->
              <option :value="null">
                {{ defaultValue(key)
                  ? t('orthanc_keep_default', 'Par défaut ({value})', { value: defaultValue(key) })
                  : t('orthanc_undefined', 'Non défini') }}
              </option>
              <option :value="true">{{ t('yes', 'Oui') }}</option>
              <option :value="false">{{ t('no', 'Non') }}</option>
            </select>
            <!-- The server declares the accepted values: a list saves
                 having to know labels such as
                 "volview-viewer-publication" by heart, and removes the
                 typo. -->
            <select
              v-else-if="allowedValues(key)"
              :id="'c-' + key" v-model="fields[key]"
            >
              <option :value="null">
                {{ defaultValue(key)
                  ? t('orthanc_keep_default', 'Par défaut ({value})', { value: defaultValue(key) })
                  : t('orthanc_undefined', 'Non défini') }}
              </option>
              <option v-for="v in allowedValues(key)" :key="v" :value="v">{{ v }}</option>
            </select>
            <textarea
              v-else-if="detectType(key) === 'list'"
              :id="'c-' + key" rows="4"
              :value="listToText(fields[key])"
              @input="textToList(key, $event.target.value)"
            ></textarea>
            <input
              v-else-if="detectType(key) === 'number'"
              :id="'c-' + key" v-model.number="fields[key]" type="number"
              :min="minBound(key)" :max="maxBound(key)"
              :placeholder="defaultValue(key)"
            >
            <input
              v-else :id="'c-' + key" v-model="fields[key]" type="text"
              :placeholder="defaultValue(key)"
            >
            <span v-if="isModified(key)" class="flag">{{ t('modified', '● modifié') }}</span>
          </div>
        </div>
      </section>

      <div class="toolbar">
        <span v-if="modifiedCount" class="compteur">
          {{ t('orthanc_pending', '{count} paramètre(s) modifié(s)', { count: modifiedCount }) }}
        </span>
        <span v-else-if="restartRequired" class="compteur compteur--attente">
          {{ t('orthanc_restart_pending', 'En attente de redémarrage pour prendre effet') }}
        </span>
        <button
          class="oe2-btn" :class="{ 'oe2-btn--primary': restartRequired }"
          :disabled="saving || restarting" @click="restart"
        >
          <i class="fa-solid fa-rotate-right"></i>
          {{ restarting
            ? t('orthanc_restarting', 'Redémarrage…')
            : t('orthanc_restart', 'Redémarrer Orthanc') }}
        </button>
        <button class="oe2-btn oe2-btn--primary" :disabled="saving || !modifiedCount" @click="save">
          <i class="fa-solid fa-check"></i>
          {{ saving
            ? t('orthanc_saving', 'Enregistrement…')
            : t('orthanc_save', 'Enregistrer les modifications') }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
h2 { font-size: var(--oe2-fs-body); margin: 0 0 6px; font-weight: 400; }
.note { color: var(--oe2-muted); font-size: var(--oe2-fs-small); margin: 0 0 18px; max-width: 80ch; }
.loading { color: var(--oe2-muted); text-align: center; padding: 20px; }

.compteur--attente { color: var(--oe2-warn, #b26a00); }
.group { margin-bottom: 22px; }
.group h3 {
  font-size: var(--oe2-fs-small); font-weight: 400; text-transform: uppercase;
  letter-spacing: 0.5px; color: var(--oe2-accent-soft);
  margin: 0 0 8px; padding-bottom: 5px;
  border-bottom: 1px solid var(--oe2-separator);
}
.group h3 i { margin-right: 7px; }

.row {
  display: grid; grid-template-columns: minmax(240px, 2fr) minmax(200px, 1fr);
  gap: 14px; align-items: start;
  padding: 7px 8px; border-left: 3px solid transparent;
}
.row--modified { border-left-color: var(--oe2-accent-orange); background: rgba(209, 155, 61, 0.07); }

.intitule { display: flex; flex-direction: column; gap: 2px; }
.intitule label { font-size: var(--oe2-fs-small); }
.tech { font-family: var(--oe2-font-mono); font-size: var(--oe2-fs-micro); color: var(--oe2-muted); }
.defaut { font-family: var(--oe2-font-stack); font-style: normal; color: var(--oe2-accent-soft); }
.hint { font-size: var(--oe2-fs-tiny); color: var(--oe2-muted); max-width: 60ch; }

.value { display: flex; align-items: center; gap: 8px; }
.value input, .value select, .value textarea {
  flex: 1; min-width: 0;
  background: var(--oe2-nav-sub-bg);
  border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color);
  padding: 5px 8px; border-radius: 3px;
  font-family: var(--oe2-font-stack); font-size: var(--oe2-fs-small);
}
.value textarea { font-family: var(--oe2-font-mono); font-size: var(--oe2-fs-tiny); resize: vertical; }
.flag { color: var(--oe2-accent-orange); font-size: var(--oe2-fs-micro); white-space: nowrap; }

.toolbar {
  position: sticky; bottom: 0;
  display: flex; align-items: center; justify-content: flex-end; gap: 12px;
  padding: 10px 8px; margin-top: 8px;
  background: var(--oe2-nav-bg);
  border-top: 1px solid var(--oe2-separator);
}
.compteur { font-size: var(--oe2-fs-tiny); color: var(--oe2-accent-orange); }

@media (max-width: 720px) {
  .row { grid-template-columns: 1fr; gap: 4px; }
}
</style>
