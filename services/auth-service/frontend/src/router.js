import { createRouter, createWebHistory } from 'vue-router'
import SetupWizard from './views/SetupWizard.vue'
import AdminHub from './views/AdminHub.vue'

// Namespace /console/ dedie, distinct de /auth/ qui appartient a Authelia.
// URLs cote navigateur :
//   /console/        → hub admin  (authentification requise)
//   /console/setup   → wizard     (accessible sans compte)
const router = createRouter({
  history: createWebHistory('/console/'),
  routes: [
    { path: '/', component: AdminHub, meta: { title: 'Administration' } },
    { path: '/setup', component: SetupWizard, meta: { title: 'Configuration initiale' } },
  ],
})

router.afterEach((to) => {
  document.title = to.meta.title
    ? `${to.meta.title} — Orthanc`
    : 'Orthanc — Admin'
})

export default router
