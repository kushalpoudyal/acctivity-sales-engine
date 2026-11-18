import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // API_URL is set by compose.yaml (the api container); locally it defaults to the FastAPI dev server.
    proxy: { '/api/v1': process.env.API_URL || 'http://127.0.0.1:9001' },
    // File-change events don't cross a Docker bind mount on Windows/macOS.
    watch: process.env.VITE_USE_POLLING === 'true' ? { usePolling: true, interval: 300 } : undefined,
  },
})
