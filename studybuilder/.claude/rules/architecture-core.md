# Core Architecture

## Application Bootstrap
- **Entry point**: `src/main.js`
- Loads runtime configuration from `/config.json` (not bundled, environment-specific)
- Initializes plugins: auth, i18n, vuetify, Pinia, router, event bus, etc.
- Configuration values are injected via `$config` and used throughout the app

## Configuration System
- Runtime config: `public/config.json` - **DO NOT modify environment files** (`.env.dev`, `.env.staging`) unless you understand the build pipeline
- Config includes API URLs, OAuth settings, feature flags, and Application Insights
- Boolean config values are parsed from strings (e.g., '1', 'true', 'yes' → true)

## State Management (Pinia)
- All stores are in `src/stores/`
- Key stores:
  - `app.js` - Application state (breadcrumbs, menu, section navigation)
  - `auth.js` - Authentication and user info
  - `studies-general.js` - Selected study context
  - `feature-flags.js` - Feature flag management
- Store composition pattern: stores import and use other stores as needed

## API Layer
- Base repository: `src/api/repository.js` (Axios instance with interceptors)
- API modules in `src/api/` follow resource-based pattern (e.g., `study.js`, `activities.js`)
- Each module exports methods that call the repository with specific endpoints
- API responses use standard REST conventions (GET, POST, PATCH, DELETE)


