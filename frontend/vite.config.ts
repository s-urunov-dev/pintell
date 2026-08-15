import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the Vite dev server proxies /api to Django, so the browser
// only ever talks to one origin (same arrangement nginx provides in Docker).
const API_TARGET = process.env.VITE_DEV_API_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
    },
  },
  // The PDF viewer's worker (`components/SourceViewer.tsx`). Built as an ES
  // module rather than Vite's default IIFE so it is emitted as an ordinary
  // hashed `.js` chunk and imported the same way as everything else — the
  // library's own `pdf.worker.min.mjs` would otherwise be served with an
  // extension nginx's mime.types has no entry for.
  worker: {
    format: 'es',
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 700,
  },
});
