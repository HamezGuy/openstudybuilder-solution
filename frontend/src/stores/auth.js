import { defineStore } from 'pinia'

import { auth } from '@/plugins/auth'

// Identity decoding and shaping live in the auth plugin (decodeTokenPayload,
// formatUserInfo, the gateway /__sso/token fetch); this store only holds state.
export const useAuthStore = defineStore('auth', {
  state: () => ({
    userInfo: null,
    displayWelcomeMsg: false,
    identityResolved: false,
  }),

  actions: {
    async initialize() {
      this.bindSessionLossListener()
      let user = null
      try {
        user = await auth.getUserInfo()
      } catch {
        user = null
      }
      this.userInfo = user
      this.identityResolved = true
    },
    bindSessionLossListener() {
      if (typeof window === 'undefined' || window.__ccStorageBound) return
      window.__ccStorageBound = true
      const leave = () => {
        const host = window.location.hostname
        const match = host.match(/^(il|osb|edc|ref)\.(.+)$/i)
        window.location.replace(
          match ? `${window.location.protocol}//www.${match[2]}/` : '/'
        )
      }
      window.addEventListener('storage', (event) => {
        if (event.key !== null && event.key !== 'cc_subject') return
        if (event.key === 'cc_subject' && event.newValue) return
        leave()
      })
      try {
        const bc = new BroadcastChannel('cc-session')
        bc.onmessage = (event) => {
          if (event?.data?.type === 'cleared') leave()
        }
      } catch {
        /* BroadcastChannel unsupported */
      }
    },
    setWelcomeMsgFlag(value) {
      this.displayWelcomeMsg = value
    },
  },
})
