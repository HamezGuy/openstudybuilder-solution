# Conventions and Common Gotchas

## File Naming Conventions

- Vue components: PascalCase (e.g., `StudyTitle.vue`, `ActivityOverview.vue`)
- JavaScript files: camelCase (e.g., `study.js`, `formRules.js`)
- Stores: kebab-case or descriptive names (e.g., `app.js`, `studies-general.js`)
- Constants: camelCase files, UPPER_CASE exports when appropriate

## Common Gotchas

1. **Routes with dots**: Version numbers in routes (e.g., `/overview/4.1`) need special handling in Vite config (see `spa-fallback-for-dots` plugin)

2. **Config.json vs .env files**:
   - `public/config.json` is for runtime configuration
   - `.env.*` files are only used during build time
   - Don't confuse the two - config.json is loaded at app startup

3. **Study context**: Many operations require a study to be selected. Always check `studiesGeneralStore.selectedStudy` before study-specific operations.

4. **Feature flags**: New features should be gated by feature flags. Check existing patterns in router and components.

5. **Authentication state**: OAuth tokens can expire. API layer handles 401s and triggers re-authentication.

6. **Async initialization**: The app waits for config.json to load before mounting. Don't try to access `$config` in module-level code.

7. **Path aliases**: Use `@/` to import from `src/` directory (configured in vite.config.js)


