import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Servida bajo nodo.host/colombia-unida/. En dev el proxy replica lo que
// hace nginx en producción: /colombia-unida/api/* → servicio api.
export default defineConfig({
  plugins: [react()],
  base: '/colombia-unida/',
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
