<script setup>
import { ref, onMounted, defineAsyncComponent } from 'vue'
import { api } from '../api.js'
import { t } from '../i18n.js'
import { useUiStore } from '../stores/ui.js'

// Lazy-load des tabs : le bundle initial reste minimal, chaque onglet
// tire son code au premier clic.
const UsersTab       = defineAsyncComponent(() => import('../components/tabs/UsersTab.vue'))
const OrthancTab     = defineAsyncComponent(() => import('../components/tabs/OrthancConfigTab.vue'))
const HealthTab      = defineAsyncComponent(() => import('../components/tabs/HealthTab.vue'))

const tabs = [
  { id: 'users',   label: t('tab_users', 'Utilisateurs'),        icon: 'fa-users',       comp: UsersTab },
  { id: 'orthanc', label: t('tab_orthanc', 'Configuration Orthanc'), icon: 'fa-server',      comp: OrthancTab },
  { id: 'health',  label: t('tab_health', 'État'),               icon: 'fa-heart-pulse', comp: HealthTab },
]
const active = ref('users')
const currentTab = () => tabs.find((onglet) => onglet.id === active.value).comp

// URLs runtime (servies par nginx, pas bundled par Vite)
const logoUrl = '/auth/static/orthanc-logo-official.png'

const imageVersion = ref('dev')
const adminUsername = ref('admin')

onMounted(async () => {
  // Ces infos sont exposees par une nouvelle route /api/admin/whoami
  try {
    const meta = await api('/console/api/admin/whoami')
    imageVersion.value = meta.image_version || 'dev'
    adminUsername.value = meta.username || 'admin'
  } catch {
    // best effort — ne bloque pas le rendu
  }
})
</script>

<template>
  <div class="oe2-app">
    <aside class="oe2-sidebar">
      <a href="/ui/app/" class="oe2-sidebar__brand" :title="t('nav_back_to_explorer', 'Retour à Orthanc Explorer')">
        <img :src="logoUrl" alt="Orthanc" class="oe2-sidebar__logo">
      </a>
      <div class="oe2-sidebar__brand-name">Orthanc</div>

      <nav class="oe2-sidebar__nav" :aria-label="t('nav_administration', 'Administration')">
        <a href="/ui/app/" class="oe2-sidebar__link">
          <i class="fa-solid fa-arrow-left" aria-hidden="true"></i>
          <span>{{ t('nav_back_to_explorer', 'Retour à Orthanc Explorer') }}</span>
        </a>
        <a href="/auth/tokens/manage" class="oe2-sidebar__link">
          <i class="fa-solid fa-share-nodes" aria-hidden="true"></i>
          <span>{{ t('nav_token_manager', 'Liens de partage') }}</span>
        </a>
        <!-- <span> plutot qu'<a href="#"> : evite le saut en haut de page au clic. -->
        <span class="oe2-sidebar__link active" role="link" aria-current="page">
          <i class="fa-solid fa-shield-halved" aria-hidden="true"></i>
          <span>{{ t('nav_administration', 'Administration') }}</span>
        </span>
      </nav>
    </aside>

    <main class="oe2-main">
      <header class="oe2-main__header">
        <h1 class="oe2-main__title">{{ t('admin_title', 'Administration') }}</h1>
        <div class="oe2-main__user">
          <i class="fa-solid fa-user" aria-hidden="true"></i>
          <span>{{ adminUsername }}</span>
        </div>
      </header>

      <nav class="tabs" role="tablist">
        <button
          v-for="onglet in tabs" :key="onglet.id"
          :class="['tab', { 'tab--active': active === onglet.id }]"
          @click="active = onglet.id"
          role="tab"
          :aria-selected="active === onglet.id"
        >
          <i :class="['fa-solid', onglet.icon]"></i> {{ onglet.label }}
        </button>
      </nav>

      <section class="panel">
        <Suspense>
          <component :is="currentTab()" />
          <template #fallback>
            <div class="loading">{{ t('loading', 'Chargement…') }}</div>
          </template>
        </Suspense>
      </section>

      <div class="oe2-main__version">auth-service v{{ imageVersion }}</div>
    </main>
  </div>
</template>

<style scoped>
/* La mise en page (barre laterale, en-tete, pied) vient d'oe2-shared.css,
   comme pour le gestionnaire de partages : c'est ce qui garantit une police,
   des couleurs et des espacements identiques a Orthanc Explorer. Ne subsiste
   ici que la barre d'onglets, absente du systeme partage. */

.tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--oe2-border-subtle);
  margin-bottom: 16px;
}
.tab {
  padding: 8px 14px;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--oe2-muted);
  font-family: var(--oe2-font-stack);
  font-size: var(--oe2-fs-small);
  cursor: pointer;
}
.tab:hover { color: var(--oe2-nav-color); }
.tab--active {
  color: var(--oe2-nav-color);
  border-bottom-color: var(--oe2-accent-orange);
}
.panel { padding: 0 16px; }
.loading { color: var(--oe2-muted); text-align: center; padding: 20px; }
</style>