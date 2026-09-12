import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'

// Plugins
import appInsights from '@/plugins/appInsights'
import auth from '@/plugins/auth'
import eventBus from '@/plugins/eventBus'
import notificationHub from '@/plugins/notificationHub'
import formRules from '@/plugins/formRules'
import i18n from '@/plugins/i18n'
import vuetify from '@/plugins/vuetify'
import draggable from '@/plugins/draggable'

// QuillEditor enhancements
import '@/plugins/quillTableBetter'

// Filters
import filters from '@/filters'

let globalConfig

/*
 * Convert some string values to more appropriate ones
 */
function prepareConfig(config) {
  const trueValues = ['1', 't', 'true', 'on', 'y', 'yes']
  for (const field of [
    'OAUTH_ENABLED',
    'OAUTH_RBAC_ENABLED',
    'APPINSIGHTS_DISABLE',
    'ENABLE_SCREEN_RECORDER',
  ]) {
    if (field in config && typeof config[field] === 'string') {
      const currentValue = config[field].trim().toLowerCase()
      config[field] = trueValues.includes(currentValue)
    }
  }
}

fetch('/config.json').then((resp) => {
  resp.json().then((config) => {
    const app = createApp(App)
    prepareConfig(config)
    globalConfig = config
    app.config.globalProperties.$config = config
    app.config.globalProperties.$filters = filters
    app.config.globalProperties.$globals = {
      historyDialogMaxWidth: '1600px',
      historyDialogFullscreen: true,
    }
    app.provide('$config', config)
    app.use(createPinia())
    app.use(router)
    app.use(auth, { config })
    app.use(appInsights, {
      config,
      router,
      trackAppErrors: true,
      cloudRole: 'frontend',
      cloudRoleInstance: 'vue-app',
    })
    app.use(eventBus)
    app.use(notificationHub)
    app.use(formRules)
    app.use(i18n).provide('$i18n', i18n)
    app.use(vuetify)
    app.use(draggable)
    app.mount('#app')
  })
})

export const useGlobalConfig = () => globalConfig
