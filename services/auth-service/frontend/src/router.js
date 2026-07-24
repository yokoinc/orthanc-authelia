import { createRouter, createWebHistory } from 'vue-router'
import SetupWizard from './views/SetupWizard.vue'
import AdminHub from './views/AdminHub.vue'

// Base = /auth/ui/ pour rester derriere le prefix nginx qui strip /auth/
// et envoie /ui/... a auth-service. Les vraies URLs cote browser :
//   /auth/ui/setup  → wizard
//   /auth/ui/admin  → hub
const router = createRouter({
  history: createWebHistory('/auth/ui/'),
  routes: [
    { path: '/', redirect: '/admin' },
    { path: '/setup', component: SetupWizard, meta: { title: 'Configuration initiale' } },
    { path: '/admin', component: AdminHub, meta: { title: 'Administration' } },
  ],
})

router.afterEach((to) => {
  document.title = to.meta.title
    ? `${to.meta.title} — Orthanc`
    : 'Orthanc — Admin'
})

export default router
