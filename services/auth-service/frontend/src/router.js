import { createRouter, createWebHistory } from 'vue-router'
import SetupWizard from './views/SetupWizard.vue'
import AdminHub from './views/AdminHub.vue'

// Dedicated /console/ namespace, distinct from /auth/ which belongs to
// Authelia. Browser-side URLs:
//   /console/        -> admin hub  (authentication required)
//   /console/setup   -> wizard     (reachable without an account)
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
