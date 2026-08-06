<script setup>
import { ref, onMounted, computed } from 'vue'
import { api } from '../../api.js'
import { t } from '../../i18n.js'
import { useUiStore } from '../../stores/ui.js'
import { GROUPES } from '../../orthanc_fields.js'

const ui = useUiStore()
const fields = ref({})
const originalFields = ref({})
const loading = ref(true)
const saving = ref(false)
const restarting = ref(false)
// Passe a true des qu'une modification est enregistree : elle reste sans
// effet tant qu'Orthanc n'a pas redemarre, et rien ne le laisse voir
// autrement -- le panel continuerait d'afficher la nouvelle valeur.
const restartRequired = ref(false)
const replies = ref({})

const meta = ref({})

// Le type vient du serveur, qui le tient de sa liste de parametres
// autorises. Le deduire de la valeur ne marchait que pour les parametres
// presents dans orthanc.json : les autres valent null et retombaient sur un
// champ texte, alors qu'il s'agit souvent de booleens.
function detectType(cle) {
  const t = meta.value[cle]?.type
  if (t === 'bool') return 'bool'
  if (t === 'int') return 'number'
  if (t === 'list') return 'list'
  return 'text'
}

// Parametre absent du fichier : Orthanc applique sa valeur par defaut. Le
// signaler evite de laisser croire a un reglage vide.
function parDefaut(cle) {
  return meta.value[cle]?.present === false
}

// La valeur par defaut, mise en forme pour l'affichage. Deux parametres ne
// figurent pas dans le fichier de reference d'Orthanc : on n'affiche alors
// rien plutot que d'avancer une valeur inventee.
function valeurDefaut(cle) {
  const d = meta.value[cle]?.default
  if (d === undefined || d === null) return ''
  if (typeof d === 'boolean') return d ? t('yes', 'Oui') : t('no', 'Non')
  if (Array.isArray(d)) return d.length ? d.join(', ') : '—'
  if (d === '') return '—'
  return String(d)
}

function isModified(key) {
  return JSON.stringify(fields.value[key]) !== JSON.stringify(originalFields.value[key])
}

// Libelle et aide d'un parametre, ou son nom technique a defaut.
function libelle(cle) {
  for (const g of GROUPES) {
    if (g.champs[cle]) return g.champs[cle][0]
  }
  return cle
}
function aide(cle) {
  for (const g of GROUPES) {
    if (g.champs[cle]) return g.champs[cle][1]
  }
  return ''
}

// Les parametres que la description ne couvre pas restent affichés dans un
// groupe « Autres » : mieux vaut un champ mal range qu'un champ invisible si
// la liste cote serveur s'enrichit.
const groupesAffiches = computed(() => {
  const connus = new Set()
  const resultat = []

  for (const g of GROUPES) {
    const cles = Object.keys(g.champs).filter((c) => c in fields.value)
    cles.forEach((c) => connus.add(c))
    if (cles.length) resultat.push({ ...g, cles })
  }

  const restants = Object.keys(fields.value).filter((c) => !connus.has(c))
  if (restants.length) {
    resultat.push({
      id: 'autres',
      titre: t('orthanc_group_other', 'Autres'),
      icone: 'fa-ellipsis',
      cles: restants,
    })
  }
  return resultat
})

const nbModifies = computed(
  () => Object.keys(fields.value).filter(isModified).length,
)

// Les listes sont editees en texte, une valeur par ligne.
function listeVersTexte(v) {
  return Array.isArray(v) ? v.join('\n') : ''
}
function texteVersListe(cle, texte) {
  fields.value[cle] = texte.split('\n').map((l) => l.trim()).filter(Boolean)
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
      <section v-for="g in groupesAffiches" :key="g.id" class="groupe">
        <h3><i :class="['fa-solid', g.icone]"></i> {{ g.titre }}</h3>

        <div
          v-for="cle in g.cles" :key="cle"
          class="row" :class="{ 'row--modified': isModified(cle) }"
        >
          <div class="intitule">
            <label :for="'c-' + cle">{{ libelle(cle) }}</label>
            <span class="tech">
              {{ cle }}
              <em v-if="parDefaut(cle) && valeurDefaut(cle)" class="defaut">
                {{ t('orthanc_default_is', '· par défaut : {value}', { value: valeurDefaut(cle) }) }}
              </em>
              <em v-else-if="parDefaut(cle)" class="defaut">{{ t('orthanc_default', '· valeur par défaut d\'Orthanc') }}</em>
            </span>
            <span v-if="aide(cle)" class="aide">{{ aide(cle) }}</span>
          </div>

          <div class="valeur">
            <select v-if="detectType(cle) === 'bool'" :id="'c-' + cle" v-model="fields[cle]">
              <!-- Le parametre absent du fichier vaut null : sans cette option
                   la liste n'avait rien a selectionner et s'affichait vide.
                   La choisir revient a ne rien ecrire, donc a laisser Orthanc
                   appliquer sa valeur par defaut. -->
              <option :value="null">
                {{ valeurDefaut(cle)
                  ? t('orthanc_keep_default', 'Par défaut ({value})', { value: valeurDefaut(cle) })
                  : t('orthanc_undefined', 'Non défini') }}
              </option>
              <option :value="true">{{ t('yes', 'Oui') }}</option>
              <option :value="false">{{ t('no', 'Non') }}</option>
            </select>
            <textarea
              v-else-if="detectType(cle) === 'list'"
              :id="'c-' + cle" rows="4"
              :value="listeVersTexte(fields[cle])"
              @input="texteVersListe(cle, $event.target.value)"
            ></textarea>
            <input
              v-else-if="detectType(cle) === 'number'"
              :id="'c-' + cle" v-model.number="fields[cle]" type="number"
              :placeholder="valeurDefaut(cle)"
            >
            <input
              v-else :id="'c-' + cle" v-model="fields[cle]" type="text"
              :placeholder="valeurDefaut(cle)"
            >
            <span v-if="isModified(cle)" class="flag">{{ t('modified', '● modifié') }}</span>
          </div>
        </div>
      </section>

      <div class="toolbar">
        <span v-if="nbModifies" class="compteur">
          {{ t('orthanc_pending', '{count} paramètre(s) modifié(s)', { count: nbModifies }) }}
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
        <button class="oe2-btn oe2-btn--primary" :disabled="saving || !nbModifies" @click="save">
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
.groupe { margin-bottom: 22px; }
.groupe h3 {
  font-size: var(--oe2-fs-small); font-weight: 400; text-transform: uppercase;
  letter-spacing: 0.5px; color: var(--oe2-accent-soft);
  margin: 0 0 8px; padding-bottom: 5px;
  border-bottom: 1px solid var(--oe2-separator);
}
.groupe h3 i { margin-right: 7px; }

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
.aide { font-size: var(--oe2-fs-tiny); color: var(--oe2-muted); max-width: 60ch; }

.valeur { display: flex; align-items: center; gap: 8px; }
.valeur input, .valeur select, .valeur textarea {
  flex: 1; min-width: 0;
  background: var(--oe2-nav-sub-bg);
  border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color);
  padding: 5px 8px; border-radius: 3px;
  font-family: var(--oe2-font-stack); font-size: var(--oe2-fs-small);
}
.valeur textarea { font-family: var(--oe2-font-mono); font-size: var(--oe2-fs-tiny); resize: vertical; }
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
