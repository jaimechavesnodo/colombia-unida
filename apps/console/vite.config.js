import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Consola servida bajo nodo.host/colombia-unida/consola/. El proxy en dev
// replica lo que hace nginx en producción: mismo origen, sin CORS.
export default defineConfig({
  plugins: [react()],
  base: '/colombia-unida/consola/',
  server: {
    proxy: {
      '/colombia-unida/api': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8099',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/colombia-unida\/api/, ''),
      },
    },
  },
})
