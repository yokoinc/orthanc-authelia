<script setup>
import { ref, onMounted, defineAsyncComponent } from 'vue'
import { api } from '../api.js'
import { t } from '../i18n.js'
import { useUiStore } from '../stores/ui.js'

// Lazy-loaded tabs: the initial bundle stays minimal, each tab pulls its
// code on first click.
const UsersTab       = defineAsyncComponent(() => import('../components/tabs/UsersTab.vue'))
const OrthancTab     = defineAsyncComponent(() => import('../components/tabs/OrthancConfigTab.vue'))
const HealthTab      = defineAsyncComponent(() => import('../components/tabs/HealthTab.vue'))
const MaintenanceTab = defineAsyncComponent(() => import('../components/tabs/MaintenanceTab.vue'))
const AuditTab       = defineAsyncComponent(() => import('../components/tabs/AuditTab.vue'))
const ModalitiesTab  = defineAsyncComponent(() => import('../components/tabs/ModalitiesTab.vue'))

const tabs = [
  { id: 'users',   label: t('tab_users', 'Utilisateurs'),        icon: 'fa-users',       comp: UsersTab },
  { id: 'orthanc', label: t('tab_orthanc', 'Configuration Orthanc'), icon: 'fa-server',      comp: OrthancTab },
  { id: 'modal',   label: t('tab_modalities', 'Équipements'),      icon: 'fa-x-ray',       comp: ModalitiesTab },
  { id: 'health',  label: t('tab_health', 'État'),               icon: 'fa-heart-pulse', comp: HealthTab },
  { id: 'maint',   label: t('tab_maintenance', 'Maintenance'),   icon: 'fa-screwdriver-wrench', comp: MaintenanceTab },
  { id: 'audit',   label: t('tab_audit', 'Journal'),            icon: 'fa-clock-rotate-left', comp: AuditTab },
]
const active = ref('users')
const currentTab = () => tabs.find((tab) => tab.id === active.value).comp

// Runtime URLs (served by nginx, not bundled by Vite)
const logoUrl = '/auth/static/orthanc-logo-official.png'

const imageVersion = ref('dev')
const adminUsername = ref('admin')
// Server name, taken from Orthanc. Falling back to 'Orthanc' avoids an
// empty banner while the answer is pending, or if Orthanc is unavailable.
const serverName = ref('Orthanc')

onMounted(async () => {
  // This information is exposed by the /api/admin/whoami route
  try {
    const meta = await api('/console/api/admin/whoami')
    imageVersion.value = meta.image_version || 'dev'
    adminUsername.value = meta.username || 'admin'
    if (meta.server_name) serverName.value = meta.server_name
  } catch {
    // best effort -- does not block rendering
  }
})
</script>

<template>
  <div class="oe2-app">
    <aside class="oe2-sidebar">
      <a href="/ui/app/" class="oe2-sidebar__brand" :title="t('nav_back_to_explorer', 'Retour à Orthanc Explorer')">
        <img :src="logoUrl" alt="Orthanc" class="oe2-sidebar__logo">
      </a>
      <div class="oe2-sidebar__brand-name">{{ serverName }}</div>

      <nav class="oe2-sidebar__nav" :aria-label="t('nav_administration', 'Administration')">
        <a href="/ui/app/" class="oe2-sidebar__link">
          <i class="fa-solid fa-arrow-left" aria-hidden="true"></i>
          <span>{{ t('nav_back_to_explorer', 'Retour à Orthanc Explorer') }}</span>
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
/* The layout (sidebar, header, footer) comes from oe2-shared.css,
   comme pour le gestionnaire de partages : c'est ce qui garantit une police,
   des couleurs et des espacements identiques a Orthanc Explorer. Ne subsiste
   ici que la barre d'onglets, absente du systeme partage. */

.tabs {
  display: flex;
  gap: 4px;
  /* Blue like Orthanc's separators, see --oe2-separator. */
  border-bottom: 1px solid var(--oe2-separator);
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