# Routing and Authentication

## Routing
- Router: `src/router/index.js`
- Two main sections: `/library` and `/studies`
- Route metadata:
  - `authRequired` - Requires authentication
  - `studyRequired` - Requires a study to be selected (stored in localStorage)
  - `featureFlag` - Route is gated by feature flag
  - `requiredPermission` - Requires specific role (see `src/constants/roles.js`)
  - `resetBreadcrumbs` - Reset breadcrumb navigation on this route
- **Dynamic extension routes**: Extensions in `src/extensions/*/router/index.js` are auto-loaded

## Authentication Flow
- OAuth/OIDC via `oidc-client-ts` library
- Auth plugin: `src/plugins/auth.js`
- Token stored in browser and validated on navigation
- Router guards check `authRequired` and `requiredPermission` metadata
- Login redirect stores intended route in sessionStorage
- Access token is automatically included in API requests via Axios interceptor


