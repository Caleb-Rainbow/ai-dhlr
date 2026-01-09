import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
// https://vite.dev/config/
export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // 开发服务器响应头 - 禁用浏览器缓存
    headers: {
      'Cache-Control': 'no-store, no-cache, must-revalidate, proxy-revalidate',
      'Pragma': 'no-cache',
      'Expires': '0',
    },
    // HMR 配置
    hmr: {
      // 当 HMR 连接失败时显示错误叠加层
      overlay: true,
    },
    proxy: {
      '/ws': {
        target: 'http://localhost:8000',
        ws: true,
        changeOrigin: true
      }
    }
  },
  // 强制清除优化依赖缓存（开发时每次启动都重新预构建）
  optimizeDeps: {
    force: true,
  },
  // 构建配置
  build: {
    // 每次构建生成新的文件名哈希
    rollupOptions: {
      output: {
        // 使用内容哈希确保文件变化时哈希也变化
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash].[ext]'
      }
    }
  }
})
