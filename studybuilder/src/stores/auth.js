import { defineStore } from 'pinia'

import { auth } from '@/plugins/auth'

/** Decode a JWT payload for display only. Authorization remains server-side. */
export function decodeJwtPayload(token) {
  const segments = String(token).split('.')
  if (segments.length !== 3 || !segments[1]) {
    throw new Error('Invalid JWT shape')
  }
  const base64 = segments[1].replace(/-/g, '+').replace(/_/g, '/')
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
  const binary = atob(padded)
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0))
  return JSON.parse(new TextDecoder().decode(bytes))
}

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
        const payload = decodeJwtPayload(body.access_token)
        return {
          ...payload,
          name:
            payload.name ||
            payload.preferred_username ||
            payload.username ||
            '',
          roles: Array.isArray(payload.roles) ? payload.roles : [],
        }
      } catch {
        return null
      }
    },
    setWelcomeMsgFlag(value) {
      this.displayWelcomeMsg = value
    },
  },
})
