import { createRouter, createWebHistory } from 'vue-router'

import NormalizeView from '@/views/NormalizeView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 单位归一是版本化清单页，先于动态模块路由匹配
    { path: '/unit_normalize', name: 'unit_normalize', component: NormalizeView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
