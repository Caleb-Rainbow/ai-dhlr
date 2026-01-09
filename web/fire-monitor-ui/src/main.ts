import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import router from './router'
import { initTheme } from './composables/useTheme'

// Initialize theme before app mounts to prevent flash
initTheme()

// 路由错误处理 - 捕获导航时的错误
router.onError((error) => {
    console.error('Router error:', error)
    // 如果是模块加载错误或 DOM 错误，刷新页面
    if (
        error.message?.includes('Failed to fetch dynamically imported module') ||
        error.message?.includes('parentNode') ||
        error.message?.includes('Loading chunk')
    ) {
        console.warn('Detected stale module error, reloading page...')
        window.location.reload()
    }
})

// 全局未捕获错误处理
window.addEventListener('unhandledrejection', (event) => {
    if (
        event.reason?.message?.includes('parentNode') ||
        event.reason?.message?.includes('Cannot read properties of null')
    ) {
        console.warn('Detected unhandled rejection with DOM error, reloading...')
        event.preventDefault()
        window.location.reload()
    }
})

createApp(App)
    .use(router)
    .mount('#app')
