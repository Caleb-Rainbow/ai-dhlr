import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
// https://vite.dev/config/
export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/status': 'http://localhost:8000',
      '/device': 'http://localhost:8000',
      '/performance': 'http://localhost:8000',
      '/cameras': 'http://localhost:8000',
      '/zones': 'http://localhost:8000',
      '/logs': 'http://localhost:8000',
      '/settings': 'http://localhost:8000',
      '/control': 'http://localhost:8000',
      '/snapshots': 'http://localhost:8000',
      '/ws': {
        target: 'http://localhost:8000',
        ws: true,
        changeOrigin: true
      }
    }
  }
})
