import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import router from './router'
import { initTheme } from './composables/useTheme'

// Initialize theme before app mounts to prevent flash
initTheme()

createApp(App)
    .use(router)
    .mount('#app')

