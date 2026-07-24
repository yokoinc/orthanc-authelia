import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Build produit dans dist/ ; le Dockerfile auth-service copie ce dist
// dans /app/frontend/ et FastAPI le sert via StaticFiles + une route
// catch-all qui renvoie index.html pour toute route SPA.
export default defineConfig({
  plugins: [vue()],
  base: '/auth/ui/',   // les assets sont servis sous /auth/ui/assets/*
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Chunks compactes pour un projet modeste (pas de vendor split premature)
    rollupOptions: {
      output: { manualChunks: undefined },
    },
  },
  server: {
    // En dev (npm run dev), proxy les /api/* vers l'auth-service local
    // pour developper le frontend contre le vrai backend Python.
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
