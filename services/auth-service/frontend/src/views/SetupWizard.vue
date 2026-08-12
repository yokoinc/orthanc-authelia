<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { api } from '../api.js'
import { t } from '../i18n.js'
import { useUiStore } from '../stores/ui.js'

const ui = useUiStore()

const form = reactive({
  username: '',
  displayname: '',
  email: '',
  password: '',
  password2: '',
  publicUrl: '',
})
const submitting = ref(false)
// Set when the domain has changed: we show the steps to follow instead of
// redirecting to an address that will no longer answer.
const done = ref('')

// Starting URL, so a change only fires if the user actually edits it.
const initialUrl = ref('')
const urlEditable = ref(false)

onMounted(async () => {
  try {
    const r = await api('/console/api/setup/network')
    initialUrl.value = r.public_url || ''
    form.publicUrl = r.public_url || ''
    urlEditable.value = r.editable
  } catch {
    // Purely optional field: if it is unavailable, the wizard must stay
    // usable to create the administrator.
    urlEditable.value = false
  }
})

const urlChanged = computed(
  () => form.publicUrl.trim() !== '' && form.publicUrl.trim() !== initialUrl.value,
)

const passwordsMatch = computed(
  () => form.password === form.password2 && form.password.length >= 12,
)

// Lists what still blocks submission, to show it to the user. A greyed-out
// button with no explanation leaves them guessing what is missing.
const blockers = computed(() => {
  const missing = []
  if (form.username.length < 3) {
    missing.push(t('setup_missing_username', 'un identifiant (3 caractères minimum)'))
  }
  if (!form.displayname.length) {
    missing.push(t('setup_missing_displayname', 'un nom affiché'))
  }
  if (!form.email.includes('@')) {
    missing.push(t('setup_missing_email', 'une adresse e-mail valide'))
  }
  if (form.password.length < 12) {
    missing.push(t('setup_missing_password', 'un mot de passe de 12 caractères minimum'))
  } else if (form.password !== form.password2) {
    missing.push(t('setup_missing_confirm', 'une confirmation identique'))
  }
  return missing
})

const canSubmit = computed(() => blockers.value.length === 0 && !submitting.value)

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    await api('/console/api/setup/create-admin', {
      method: 'POST',
      body: {
        username: form.username,
        displayname: form.displayname,
        email: form.email,
        password: form.password,
        groups: ['admin'],
      },
    })
    // The public URL is changed before finalisation, while the setup window
    // is open. It only takes effect when the stack restarts.
    let newUrl = null
    if (urlChanged.value) {
      const r = await api('/console/api/setup/network', {
        method: 'POST',
        body: { public_url: form.publicUrl.trim() },
      })
      if (!r.unchanged) newUrl = r.public_url
    }

    await api('/console/api/setup/finalize', { method: 'POST' })

    if (newUrl) {
      // No redirect: the current address will not answer after the restart,
      // and the session cookie is bound to the previous domain.
      done.value = newUrl
      submitting.value = false
      return
    }
    ui.notify(t('setup_admin_created', 'Administrateur créé, redirection…'), 'ok')
    setTimeout(() => { window.location.href = '/console/' }, 1500)
  } catch (e) {
    ui.notify(e.message, 'err')
    submitting.value = false
  }
}
</script>

<template>
  <div class="setup">
    <h1>
      <i class="fa-solid fa-shield-halved" aria-hidden="true"></i>
      {{ t('setup_title', 'Configuration initiale') }}
    </h1>
    <p class="subtitle">
      {{ t('setup_subtitle', "Premier démarrage — création du compte administrateur. Ce compte pourra ensuite gérer les autres utilisateurs depuis le panel d'administration.") }}
    </p>

    <div v-if="done" class="fini">
      <p><strong>{{ t('setup_done_title', 'Installation terminée.') }}</strong></p>
      <p>{{ t('setup_done_restart', 'Relancer la pile pour appliquer le nouveau domaine :') }}</p>
      <pre>docker compose up -d</pre>
      <p>
        {{ t('setup_done_connect', 'Puis se connecter sur') }}
        <a :href="done">{{ done }}</a>.
      </p>
    </div>

    <form v-else @submit.prevent="submit">
      <label for="username">{{ t('setup_username_label', 'Identifiant') }}</label>
      <input
        id="username" v-model="form.username" required
        pattern="[a-zA-Z0-9._-]{3,32}" placeholder="prenom.nom"
      >
      <div class="hint">
        {{ t('setup_username_hint', '3 à 32 caractères : lettres, chiffres, point, tiret, tiret bas.') }}
      </div>

      <label for="displayname">{{ t('setup_displayname_label', 'Nom affiché') }}</label>
      <input
        id="displayname" v-model="form.displayname" required
        maxlength="100" :placeholder="t('setup_displayname_example', 'Prénom Nom')"
      >

      <label for="email">{{ t('setup_email_label', 'Adresse e-mail') }}</label>
      <input
        id="email" v-model="form.email" type="email" required
        placeholder="admin@exemple.fr"
      >

      <label for="password">{{ t('setup_password_label', 'Mot de passe') }}</label>
      <input
        id="password" v-model="form.password" type="password" required
        minlength="12" :placeholder="t('setup_password_example', '12 caractères minimum')"
      >
      <div class="hint">
        {{ t('setup_password_hint', 'Haché en argon2id avant écriture dans users_database.yml.') }}
      </div>

      <label for="password2">{{ t('setup_password2_label', 'Confirmation') }}</label>
      <input
        id="password2" v-model="form.password2" type="password" required minlength="12"
      >
      <div v-if="form.password2 && !passwordsMatch" class="hint hint--err">
        {{ t('setup_password_mismatch', 'Les deux mots de passe diffèrent.') }}
      </div>

      <label for="publicUrl">{{ t('setup_public_url_label', 'URL publique') }}</label>
      <input
        id="publicUrl" v-model="form.publicUrl" :disabled="!urlEditable"
        placeholder="https://pacs.exemple.fr"
      >
      <div v-if="!urlEditable" class="hint">
        {{ t('setup_public_url_locked', "Non modifiable ici : le fichier .env n'est pas monté dans le conteneur.") }}
      </div>
      <div v-else-if="urlChanged" class="hint hint--warn">
        {{ t('setup_public_url_warning', 'Le domaine va changer. Il faudra relancer la pile puis se reconnecter sur cette adresse : la session en cours est liée à l\'ancien domaine.') }}
      </div>
      <div v-else class="hint">
        {{ t('setup_public_url_hint', 'Adresse par laquelle le PACS sera joint. À laisser telle quelle pour rester en local ; modifiable plus tard depuis le panel.') }}
      </div>

      <div v-if="blockers.length" class="blockers">
        {{ t('setup_missing', 'Il manque encore {liste}.', { liste: blockers.join(', ') }) }}
      </div>

      <div class="actions">
        <button type="submit" class="oe2-btn oe2-btn--primary" :disabled="!canSubmit">
          {{ submitting
            ? t('setup_submitting', 'Création…')
            : t('setup_submit', "Créer l'administrateur et terminer") }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.setup {
  max-width: 520px;
  margin: 60px auto;
  padding: 32px 28px;
  background: var(--oe2-card-bg);
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 4px;
}
h1 {
  font-size: var(--oe2-fs-title);
  font-weight: 400;
  margin: 0 0 8px;
}
.subtitle {
  color: var(--oe2-muted);
  font-size: var(--oe2-fs-medium);
  margin: 0 0 24px;
}
label {
  display: block;
  font-size: var(--oe2-fs-small);
  color: var(--oe2-muted);
  margin: 12px 0 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
input {
  width: 100%;
  background: var(--oe2-nav-sub-bg);
  border: 1px solid var(--oe2-border-subtle);
  color: var(--oe2-nav-color);
  padding: 8px 10px;
  border-radius: 3px;
  font-size: var(--oe2-fs-medium);
  box-sizing: border-box;
}
input:focus { border-color: var(--oe2-accent); outline: none; }
input:disabled { opacity: 0.55; cursor: not-allowed; }
.hint {
  font-size: var(--oe2-fs-tiny);
  color: var(--oe2-muted);
  margin-top: 4px;
}
.hint--err { color: #ff8080; }
.hint--warn { color: #e8c98a; }
.fini {
  font-size: var(--oe2-fs-medium);
  line-height: 1.6;
}
.fini pre {
  background: var(--oe2-nav-sub-bg);
  border: 1px solid var(--oe2-border-subtle);
  padding: 8px 10px;
  border-radius: 3px;
  font-size: var(--oe2-fs-small);
  overflow-x: auto;
}
.fini a { color: var(--oe2-accent); }
.blockers {
  margin-top: 20px;
  padding: 10px 12px;
  border-left: 3px solid var(--oe2-accent-orange);
  background: rgba(209, 155, 61, 0.12);
  font-size: var(--oe2-fs-small);
  color: #e8c98a;
  border-radius: 2px;
}
.actions {
  margin-top: 24px;
  display: flex;
  justify-content: flex-end;
}
</style>
