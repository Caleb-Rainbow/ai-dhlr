import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router';
import Layout from '../components/Layout.vue';
import Dashboard from '../views/Dashboard.vue';
import Cameras from '../views/Cameras.vue';
import Zones from '../views/Zones.vue';
import Logs from '../views/Logs.vue';
import Settings from '../views/Settings.vue';
import Patrol from '../views/Patrol.vue';

const routes: RouteRecordRaw[] = [
    {
        path: '/',
        component: Layout,
        children: [
            { path: '', name: 'Dashboard', component: Dashboard },
            { path: 'cameras', name: 'Cameras', component: Cameras },
            { path: 'zones', name: 'Zones', component: Zones },
            { path: 'patrol', name: 'Patrol', component: Patrol },
            { path: 'logs', name: 'Logs', component: Logs },
            { path: 'settings', name: 'Settings', component: Settings },
        ]
    }
];

const router = createRouter({
    history: createWebHashHistory(),
    routes
});

export default router;
