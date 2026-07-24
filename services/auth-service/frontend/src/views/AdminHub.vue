<script setup>
import { ref, onMounted, defineAsyncComponent } from 'vue'
import { api } from '../api.js'
import { useUiStore } from '../stores/ui.js'

// Lazy-load des tabs : le bundle initial reste minimal, chaque onglet
// tire son code au premier clic.
const UsersTab       = defineAsyncComponent(() => import('../components/tabs/UsersTab.vue'))
const OrthancTab     = defineAsyncComponent(() => import('../components/tabs/OrthancConfigTab.vue'))
const HealthTab      = defineAsyncComponent(() => import('../components/tabs/HealthTab.vue'))

const tabs = [
  { id: 'users',   label: 'Users',          icon: 'fa-users',       comp: UsersTab },
  { id: 'orthanc', label: 'Orthanc config', icon: 'fa-server',      comp: OrthancTab },
  { id: 'health',  label: 'Health',         icon: 'fa-heart-pulse', comp: HealthTab },
]
const active = ref('users')
const currentTab = () => tabs.find((t) => t.id === active.value).comp

// URLs runtime (servies par nginx, pas bundled par Vite)
const logoUrl = '/auth/static/orthanc-logo-official.png'

const imageVersion = ref('dev')
const adminUsername = ref('admin')

onMounted(async () => {
  // Ces infos sont exposees par une nouvelle route /api/admin/whoami
  try {
    const meta = await api('/api/admin/whoami')
    imageVersion.value = meta.image_version || 'dev'
    adminUsername.value = meta.username || 'admin'
  } catch {
    // best effort — ne bloque pas le rendu
  }
})
</script>

<template>
  <div class="hub">
    <aside class="sidebar">
      <div class="brand">
        <img :src="logoUrl" alt="Orthanc" class="logo">
        <div class="brand-name">Orthanc</div>
      </div>
      <nav>
        <a href="/ui/app/" class="link">
          <i class="fa-solid fa-arrow-left"></i><span>Retour à Orthanc Explorer</span>
        </a>
        <a href="/auth/tokens/manage" class="link">
          <i class="fa-solid fa-share-nodes"></i><span>Token Manager</span>
        </a>
        <span class="link link--active" role="link" aria-current="page">
          <i class="fa-solid fa-shield-halved"></i><span>Administration</span>
        </span>
      </nav>
    </aside>

    <main class="main">
      <header class="header">
        <h1>Administration</h1>
        <div class="user">
          <i class="fa-solid fa-user"></i>
          <span>{{ adminUsername }}</span>
        </div>
      </header>

      <nav class="tabs" role="tablist">
        <button
          v-for="t in tabs" :key="t.id"
          :class="['tab', { 'tab--active': active === t.id }]"
          @click="active = t.id"
          role="tab"
          :aria-selected="active === t.id"
        >
          <i :class="['fa-solid', t.icon]"></i> {{ t.label }}
        </button>
      </nav>

      <section class="panel">
        <Suspense>
          <component :is="currentTab()" />
          <template #fallback>
            <div class="loading">Chargement…</div>
          </template>
        </Suspense>
      </section>

      <div class="version">auth-service v{{ imageVersion }}</div>
    </main>
  </div>
</template>

<style scoped>
.hub {
  display: flex;
  min-height: 100vh;
}
.sidebar {
  width: 260px;
  background: var(--oe2-nav-bg);
  border-right: 1px solid var(--oe2-border-subtle);
  display: flex;
  flex-direction: column;
}
.brand {
  padding: 20px 16px 12px;
  border-bottom: 1px solid var(--oe2-border-subtle);
  text-align: center;
}
.logo {
  height: 48px;
  filter: brightness(50);
}
.brand-name {
  font-size: 14px;
  margin-top: 8px;
}
nav .link {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  color: var(--oe2-nav-color);
  text-decoration: none;
  line-height: 35px;
  font-size: 13px;
  border-left: 3px solid transparent;
}
nav .link:hover, nav .link--active {
  background: #4f5b69;
  border-left-color: var(--oe2-accent-orange);
}
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--oe2-nav-bg);
}
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--oe2-border-subtle);
}
h1 {
  font-size: 18px;
  font-weight: 400;
  margin: 0;
}
.user {
  display: flex;
  gap: 8px;
  align-items: center;
  color: var(--oe2-muted);
  font-size: 12px;
}
.tabs {
  display: flex;
  gap: 4px;
  padding: 0 16px;
  border-bottom: 1px solid var(--oe2-border-subtle);
}
.tab {
  background: transparent;
  border: none;
  padding: 10px 14px;
  color: var(--oe2-muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
}
.tab--active {
  color: var(--oe2-nav-color);
  border-bottom-color: var(--oe2-accent-orange);
}
.panel {
  flex: 1;
  padding: 20px 16px;
}
.loading {
  color: var(--oe2-muted);
  padding: 20px;
  text-align: center;
}
.version {
  padding: 12px 16px;
  font-size: 11px;
  color: var(--oe2-muted);
  text-align: right;
  opacity: 0.6;
}
</style>
