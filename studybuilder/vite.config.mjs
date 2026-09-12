import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'

// https://vitejs.dev/config/
export default defineConfig({
  server: {
    host: true,
  },
  css: {
    preprocessorOptions: {
      sass: {
        api: 'modern-compiler',
        silenceDeprecations: ['if-function'],
      },
      scss: {
        api: 'modern-compiler',
        silenceDeprecations: ['if-function'],
      },
    },
  },
  plugins: [
    vue(),
    vuetify({ styles: { configFile: 'src/styles/settings.scss' } }),
    {
      name: 'spa-fallback-for-dots',
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          // Handle routes with version numbers (e.g., /overview/4.1, /parent-class-overview/1.0)
          // Vite treats dots as file extensions, so we need to handle these specially
          if (req.url.match(/\/\d+\.\d+$/)) {
            req.url = '/'
          }
          next()
        })
      },
    },
  ],
  assetsInclude: ['**/*.md'],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
})
