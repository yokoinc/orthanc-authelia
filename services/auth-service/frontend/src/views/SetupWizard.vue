<script setup>
import { ref, reactive, computed } from 'vue'
import { api } from '../api.js'
import { useUiStore } from '../stores/ui.js'

const ui = useUiStore()

const form = reactive({
  username: '',
  displayname: '',
  email: '',
  password: '',
  password2: '',
})
const submitting = ref(false)

const passwordsMatch = computed(
  () => form.password === form.password2 && form.password.length >= 12,
)

// Liste ce qui empeche encore la soumission, pour l'afficher a l'utilisateur.
// Un bouton grise sans explication laisse deviner ce qui manque.
const blockers = computed(() => {
  const missing = []
  if (form.username.length < 3) missing.push('un login (3 caractères minimum)')
  if (!form.displayname.length) missing.push('un nom affiché')
  if (!form.email.includes('@')) missing.push('un email valide')
  if (form.password.length < 12) missing.push('un mot de passe de 12 caractères minimum')
  else if (form.password !== form.password2) missing.push('une confirmation identique')
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
        groups: ['admins'],
      },
    })
    await api('/console/api/setup/finalize', { method: 'POST' })
    ui.notify('Admin cree, redirection vers /auth/admin…', 'ok')
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
      Configuration initiale
    </h1>
    <p class="subtitle">
      Premier démarrage — création du compte administrateur.
      Ce compte pourra ensuite gérer les autres users depuis le hub Admin.
    </p>

    <form @submit.prevent="submit">
      <label for="username">Login</label>
      <input
        id="username" v-model="form.username" required
        pattern="[a-zA-Z0-9._-]{3,32}" placeholder="cuffel.gregory"
      >
      <div class="hint">3-32 caractères, alphanumériques + . _ -</div>

      <label for="displayname">Nom affiché</label>
      <input
        id="displayname" v-model="form.displayname" required
        maxlength="100" placeholder="Grégory Cuffel"
      >

      <label for="email">Email</label>
      <input
        id="email" v-model="form.email" type="email" required
        placeholder="cuffel.gregory@gmail.com"
      >

      <label for="password">Mot de passe</label>
      <input
        id="password" v-model="form.password" type="password" required
        minlength="12" placeholder="min 12 caractères"
      >
      <div class="hint">Hashé argon2id avant écriture dans users_database.yml</div>

      <label for="password2">Confirmation</label>
      <input
        id="password2" v-model="form.password2" type="password" required minlength="12"
      >
      <div v-if="form.password2 && !passwordsMatch" class="hint hint--err">
        Les mots de passe ne correspondent pas.
      </div>

      <div v-if="blockers.length" class="blockers">
        Il manque encore {{ blockers.join(', ') }}.
      </div>

      <div class="actions">
        <button type="submit" class="btn btn--primary" :disabled="!canSubmit">
          {{ submitting ? 'Création…' : "Créer l'admin et finaliser" }}
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
  font-size: 20px;
  font-weight: 400;
  margin: 0 0 8px;
}
.subtitle {
  color: var(--oe2-muted);
  font-size: 13px;
  margin: 0 0 24px;
}
label {
  display: block;
  font-size: 12px;
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
  font-size: 13px;
  box-sizing: border-box;
}
input:focus { border-color: var(--oe2-accent); outline: none; }
.hint {
  font-size: 11px;
  color: var(--oe2-muted);
  margin-top: 4px;
}
.hint--err { color: #ff8080; }
.blockers {
  margin-top: 20px;
  padding: 10px 12px;
  border-left: 3px solid var(--oe2-accent-orange);
  background: rgba(209, 155, 61, 0.12);
  font-size: 12px;
  color: #e8c98a;
  border-radius: 2px;
}
.actions {
  margin-top: 24px;
  display: flex;
  justify-content: flex-end;
}
.btn {
  padding: 8px 16px;
  border-radius: 3px;
  border: none;
  cursor: pointer;
  font-size: 13px;
}
.btn--primary {
  background: var(--oe2-accent);
  color: white;
}
.btn--primary:hover:not(:disabled) { background: var(--oe2-accent-soft); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
