import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router';
import Layout from '../components/Layout.vue';
import Dashboard from '../views/Dashboard.vue';
import Cameras from '../views/Cameras.vue';
import Zones from '../views/Zones.vue';
import Logs from '../views/Logs.vue';
import Settings from '../views/Settings.vue';
import Patrol from '../views/Patrol.vue';
import Connect from '../views/Connect.vue';

// 提取公共子路由配置
const commonChildren = [
    { path: 'dashboard', name: 'Dashboard', component: Dashboard },
    { path: 'cameras', name: 'Cameras', component: Cameras },
    { path: 'zones', name: 'Zones', component: Zones },
    { path: 'patrol', name: 'Patrol', component: Patrol },
    { path: 'logs', name: 'Logs', component: Logs },
    { path: 'settings', name: 'Settings', component: Settings },
];

const routes: RouteRecordRaw[] = [
    // 默认重定向到连接页面
    {
        path: '/',
        redirect: '/connect'
    },
    // 连接页面（独立页面，不使用 Layout）
    {
        path: '/connect',
        name: 'Connect',
        component: Connect
    },
    // 服务器模式路由（带 deviceId 前缀）
    {
        path: '/device/:deviceId',
        component: Layout,
        children: commonChildren.map(route => ({
            ...route,
            name: `Server${route.name}` // 动态重命名防止冲突
        }))
    },
    // 设备模式路由
    {
        path: '/local',
        component: Layout,
        children: [
            { path: '', redirect: '/local/dashboard' },
            ...commonChildren
        ]
    }
];

const router = createRouter({
    history: createWebHashHistory(),
    routes
});

export default router;
