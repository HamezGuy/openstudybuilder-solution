---
paths:
  - common/auth/**
---
# Authentication & Authorization

- **OAuth 2.0** via Azure AD (configurable via `.env`)
- **RBAC**: Role-based access control (when `OAUTH_RBAC_ENABLED=true`)
- **MS Graph Integration**: Optional group discovery via Microsoft Graph API
- **Swagger UI Auth**: Separate `OAUTH_SWAGGER_APP_ID` for built-in docs authentication

See `doc/Auth.md` for detailed setup.


