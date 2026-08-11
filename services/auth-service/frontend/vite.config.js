import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The build lands in dist/; the auth-service Dockerfile copies it into
// /app/frontend/ and FastAPI serves it through StaticFiles plus a catch-all
// route returning index.html for any SPA route.
export default defineConfig({
  plugins: [vue()],
  base: '/console/',   // assets are served under /console/assets/*
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Compact chunks for a modest project (no premature vendor split)
    rollupOptions: {
      output: { manualChunks: undefined },
    },
  },
  server: {
    // In dev (npm run dev), proxy /api/* to the local auth-service so the
    // frontend can be developed against the real Python backend.
    proxy: {
      '/console/api': 'http://localhost:8000',
    },
  },
})
