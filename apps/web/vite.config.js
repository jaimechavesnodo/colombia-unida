import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Servida bajo nodo.host/colombia-unida/
export default defineConfig({
  plugins: [react()],
  base: '/colombia-unida/',
  server: {
    proxy: {
      '/colombia-unida/api': {
        target: 'http://localhost:8000',
        rewrite: (path) => path.replace(/^\/colombia-unida\/api/, ''),
      },
    },
  },
})
