import { defineStore } from 'pinia'

import { auth } from '@/plugins/auth'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    userInfo: null,
    displayWelcomeMsg: false,
  }),

  actions: {
    async initialize() {
      const userInfo = await auth.getUserInfo()
      this.userInfo = userInfo || (await this.fetchGatewayIdentity())
    },
    // Behind the Command Center gateway the standalone OAuth UI is disabled,
    // but the gateway exposes the session identity on our own origin. The
    // token payload carries the same name/roles claims the OAuth path yields.
    // Standalone deployments simply 404/401 here and stay anonymous.
    async fetchGatewayIdentity() {
      try {
        const resp = await fetch('/__sso/token', { credentials: 'include' })
        if (!resp.ok) return null
        const body = await resp.json()
        if (!body || !body.access_token) return null
        return JSON.parse(atob(body.access_token.split('.')[1]))
      } catch {
        return null
      }
    },
    setWelcomeMsgFlag(value) {
      this.displayWelcomeMsg = value
    },
  },
})
