# Important Patterns

## Study Selection
- Many routes require a selected study (`studyRequired: true` in route meta)
- Selected study is stored in localStorage and loaded into `studies-general` store
- Study UID is in route params as `:study_id`
- If study is not selected, user is redirected to study selection page

## Feature Flags
- Feature flags control visibility of routes and features
- Fetched from API at router navigation
- Route-level: `meta: { featureFlag: 'flag_name' }`
- Component-level: Check `featureFlagsStore.getFeatureFlag('flag_name')`

## Breadcrumb Navigation
- Managed by `app` store
- Routes with `resetBreadcrumbs: true` clear existing breadcrumbs
- Breadcrumbs auto-generated from menu structure based on current route
- Section is set based on route's top-level path (/library, /studies, /administration)

## API Request Patterns
When making API calls:
1. Import the relevant API module (e.g., `import study from '@/api/study'`)
2. Use async/await or promises
3. Handle errors with try/catch or `.catch()`
4. Common pattern: show loading state, call API, update store/component state, handle errors

## Event Bus
- Plugin: `src/plugins/eventBus.js`
- Use `eventBusOn('eventName', handler)` and `eventBusEmit('eventName', data)`
- Common events: `userSignedIn`, notification events

## Form Validation
- Form rules plugin: `src/plugins/formRules.js`
- Provides reusable validation rules (required, email, etc.)
- Use with Vuetify form components


