import { inject } from 'vue'
import { useAuthStore } from '@/stores/auth'

export function useAccessGuard() {
  const authStore = useAuthStore()
  const $config = inject('$config')

  function checkPermission(permission) {
    if (authStore.identityResolved === false) return false
    const roles = authStore.userInfo?.roles
    // Gateway SSO seeds roles even when the standalone OAuth UI is off.
    if (Array.isArray(roles) && roles.length > 0) {
      return roles.includes(permission)
    }
    if (authStore.userInfo) return false
    if ($config?.OAUTH_ENABLED && $config?.OAUTH_RBAC_ENABLED) {
      // userInfo is normally populated by authStore.initialize() in the
      // router guard before any route renders; a transient null (mid-navigation,
      // expired token about to redirect) fails closed instead of throwing.
      return false
    }
    // Genuine standalone deployments without Command Center identity.
    return true
  }

  return {
    userInfo: authStore.userInfo,
    checkPermission,
  }
}
