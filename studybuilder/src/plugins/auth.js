import { eventBusEmit } from './eventBus'
import { UserManager } from 'oidc-client-ts'
import roles from '@/constants/roles'
import { Buffer } from 'buffer'

let manager = null
let gatewayToken = null
let gatewayTokenExp = 0
let gatewayTokenInflight = null

function decodeTokenPayload(token) {
  const segments = String(token).split('.')
  if (segments.length !== 3 || !segments[1]) {
    throw new Error('Invalid JWT shape')
  }
  const base64 = segments[1].replace(/-/g, '+').replace(/_/g, '/')
  const padded = base64.padEnd(
    base64.length + ((4 - (base64.length % 4)) % 4),
    '='
  )
  const binary = atob(padded)
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0))
  return JSON.parse(new TextDecoder().decode(bytes))
}

function formatUserInfo(payload) {
  if (!payload || typeof payload !== 'object') return null
  return {
    ...payload,
    name:
      payload.name ||
      payload.preferred_username ||
      payload.username ||
      '',
    roles: Array.isArray(payload.roles) ? payload.roles : [],
  }
}

async function getGatewayAccessToken() {
  const now = Math.floor(Date.now() / 1000)
  if (gatewayToken && gatewayTokenExp - 30 > now) {
    return gatewayToken
  }
  if (gatewayTokenInflight) {
    return gatewayTokenInflight
  }
  gatewayTokenInflight = (async () => {
    try {
      const resp = await fetch('/__sso/token', { credentials: 'include' })
      if (!resp.ok) return null
      const body = await resp.json()
      if (!body?.access_token) return null
      gatewayToken = body.access_token
      try {
        gatewayTokenExp = Number(decodeTokenPayload(gatewayToken).exp) || 0
      } catch {
        gatewayTokenExp = now + 60
      }
      return gatewayToken
    } catch {
      return null
    } finally {
      gatewayTokenInflight = null
    }
  })()
  return gatewayTokenInflight
}

const authInterface = {
  validateAccess: function (to) {
    if (!manager) return
    manager.getUser().then((user) => {
      if (!user || user.expired) {
        if (to.name !== 'Login') {
          sessionStorage.setItem('next', to.name)
          sessionStorage.setItem('nextParams', JSON.stringify(to.params))
        }
        manager.signinRedirect()
      }
    })
  },
  oauthLoginCallback: function () {
    return manager.signinRedirectCallback().then(() => {
      eventBusEmit('userSignedIn')
    })
  },
  clear: function () {
    gatewayToken = null
    gatewayTokenExp = 0
    if (!manager) return
    manager.clearStaleState()
  },
  getAccessToken: async function () {
    if (manager) {
      const user = await manager.getUser()
      if (user?.access_token) {
        return user.access_token
      }
    }
    // Command Center owns login. When the Vue OAuth UI is disabled, axios still
    // needs the short-lived audience token from the managed origin.
    return getGatewayAccessToken()
  },
  getUserInfo: async function () {
    if (manager) {
      const user = await manager.getUser()
      if (user && !user.expired && user.access_token) {
        try {
          return formatUserInfo(
            JSON.parse(
              Buffer.from(user.access_token.split('.')[1], 'base64').toString()
            )
          )
        } catch {
          return null
        }
      }
    }
    const token = await getGatewayAccessToken()
    if (!token) return null
    try {
      return formatUserInfo(decodeTokenPayload(token))
    } catch {
      return null
    }
  },
  oauthLogout: async function () {
    if (!manager) return
    return manager.signoutRedirect()
  },
}

export default {
  install: (app, options) => {
    const oauthEnabled = !!options?.config?.OAUTH_ENABLED
    if (oauthEnabled) {
      manager = new UserManager({
        metadataUrl: options.config.OAUTH_METADATA_URL,
        authority: 'studybuilder-frontend',
        client_id: options.config.OAUTH_UI_APP_ID,
        redirect_uri: location.origin + '/oauth-callback',
        response_type: 'code',
        response_mode: 'fragment',
        post_logout_redirect_uri: location.origin,
        scope: `openid profile email offline_access api://${options.config.OAUTH_API_APP_ID}/API.call`,
      })
    }
    app.config.globalProperties.$auth = authInterface
    app.config.globalProperties.$roles = roles
    app.provide('roles', roles)
  },
}

export const auth = authInterface
