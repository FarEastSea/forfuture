import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/qq' },
    { path: '/qq', component: () => import('@/views/QQTimeline.vue') },
    { path: '/xhs', component: () => import('@/views/XHSFeed.vue') },
    { path: '/knowledge', component: () => import('@/views/Knowledge.vue') },
    { path: '/chat', component: () => import('@/views/AIChat.vue') },
    { path: '/tasks', component: () => import('@/views/TaskManager.vue') },
    { path: '/settings', component: () => import('@/views/Settings.vue') },
  ],
})

export default router
