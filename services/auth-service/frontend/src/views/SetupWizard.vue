<script setup>
import { ref, reactive, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { useUiStore } from '../stores/ui.js'

const router = useRouter()
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
const canSubmit = computed(
  () =>
    form.username.length >= 3 &&
    form.displayname.length > 0 &&
    form.email.includes('@') &&
    passwordsMatch.value &&
    !submitting.value,
)

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    await api('/auth/setup/create-admin', {
      method: 'POST',
      body: {
        username: form.username,
        displayname: form.displayname,
        email: form.email,
        password: form.password,
        groups: ['admins'],
      },
    })
    await api('/auth/setup/finalize', { method: 'POST' })
    ui.notify('Admin cree, redirection vers /auth/admin…', 'ok')
    setTimeout(() => { window.location.href = '/auth/admin' }, 1500)
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
