<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../../api.js'
import { t } from '../../i18n.js'
import { useUiStore } from '../../stores/ui.js'

const ui = useUiStore()
const checks = ref({})
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const data = await api('/console/api/admin/health')
    checks.value = data.checks
  } catch (e) {
    ui.notify(t('health_error', 'Erreur lors du diagnostic : {detail}', { detail: e.message }), 'err')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h2>{{ t('health_title', 'État des composants') }}</h2>

    <div v-if="loading" class="loading">{{ t('loading', 'Chargement…') }}</div>

    <table v-else class="table">
      <thead>
        <tr>
          <th>{{ t('health_col_component', 'Composant') }}</th>
          <th>{{ t('health_col_status', 'Statut') }}</th>
          <th>{{ t('health_col_detail', 'Détail') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(info, name) in checks" :key="name">
          <td><strong>{{ name }}</strong></td>
          <td>
            <span v-if="info.ok" class="ok">
              <i class="fa-solid fa-check"></i> {{ t('health_ok', 'OK') }}
            </span>
            <span v-else class="err">
              <i class="fa-solid fa-xmark"></i> {{ t('health_ko', 'En échec') }}
            </span>
          </td>
          <td class="mono">{{ info.detail }}</td>
        </tr>
      </tbody>
    </table>

    <button class="oe2-btn oe2-btn--secondary" @click="load">
      <i class="fa-solid fa-rotate"></i> {{ t('refresh', 'Rafraîchir') }}
    </button>
  </div>
</template>

<style scoped>
h2 { font-size: 14px; margin: 0 0 12px; font-weight: 400; }
.loading { color: var(--oe2-muted); text-align: center; padding: 20px; }
.table { width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 12px; }
.table th, .table td {
  padding: 6px 10px; text-align: left;
  border-bottom: 1px solid var(--oe2-border-subtle);
}
.table th {
  color: var(--oe2-muted); text-transform: uppercase;
  letter-spacing: 0.5px; font-weight: 400; font-size: 11px;
}
.ok  { color: var(--oe2-success); }
.err { color: var(--oe2-danger); }
.mono { font-family: var(--oe2-font-mono); font-size: 11px; color: var(--oe2-muted); }
</style>
