import { inject } from 'vue'
import { useAuthStore } from '@/stores/auth'

export function useAccessGuard() {
  const authStore = useAuthStore()

  function checkPermission(permission) {
    if (authStore.identityResolved === false) return false
    const roles = authStore.userInfo?.roles
    // Gateway SSO seeds roles even when the standalone OAuth UI is off.
    if (Array.isArray(roles) && roles.length > 0) {
      return roles.includes(permission)
    }
    if (authStore.userInfo) return false
    const $config = inject('$config')
    if ($config?.OAUTH_ENABLED && $config?.OAUTH_RBAC_ENABLED) {
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
