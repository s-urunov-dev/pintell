import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the console's dev server proxies /api to Django, so the
// browser stays on one origin and the session cookie keeps SameSite=Lax.
const API_TARGET = process.env.VITE_DEV_API_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    host: true,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
