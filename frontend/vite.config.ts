import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ArcoResolver } from 'unplugin-vue-components/resolvers'
import { resolve } from 'path'

export default defineConfig({
  plugins: [
    vue(),
    Components({
      dts: false,
      resolvers: [ArcoResolver({ importStyle: 'css' })],
    }),
  ],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) {
            return
          }
          if (id.includes('@arco-design/web-vue/es/icon') || id.includes('@arco-design/web-vue/es/locale')) {
            return 'arco-core'
          }
          if (id.includes('@arco-design/web-vue')) {
            const match = id.match(/@arco-design\/web-vue\/es\/([^/]+)/)
            const componentName = match?.[1] || 'core'
            if (
              componentName.startsWith('_') ||
              ['config-provider', 'form', 'grid', 'input-tag', 'tag', 'tooltip', 'trigger'].includes(componentName)
            ) {
              return 'arco-core'
            }
            return `arco-${componentName}`
          }
          if (id.includes('vue-router')) {
            return 'router'
          }
          if (id.includes('pinia')) {
            return 'pinia'
          }
          if (id.includes('markdown-it')) {
            return 'markdown'
          }
          if (id.includes('axios')) {
            return 'network'
          }
          if (id.includes('vue')) {
            return 'vue-core'
          }
          return 'vendor'
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': { target: 'http://127.0.0.1:18100', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:18100', ws: true },
      '/static': { target: 'http://127.0.0.1:18100', changeOrigin: true },
    },
  },
})
