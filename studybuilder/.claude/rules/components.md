# Components and UI

## Component Structure
```
src/
├── components/       # Shared/reusable components
│   ├── layout/      # Layout components (AppBar, Drawer, PassThrough)
│   ├── library/     # Library-specific components
│   └── studies/     # Study-specific components
├── views/           # Page-level components (routed)
│   ├── library/     # Library section pages
│   ├── studies/     # Studies section pages
│   └── administration/ # Admin pages
├── composables/     # Vue 3 composition API reusable logic
├── plugins/         # Vue plugins (auth, i18n, vuetify, etc.)
├── utils/           # Utility functions
├── constants/       # Static constants and enums
└── extensions/      # Extensibility system (custom modules)
```

## Extensions System
- Located in `src/extensions/`
- Each extension is a self-contained module with optional:
  - `router/index.js` - Adds routes via `addExtensionRoutes(routes)` function
  - Components, views, stores specific to the extension
- Extensions are auto-discovered and loaded at build time

## Internationalization (i18n)
- Plugin: `src/plugins/i18n.js`
- Locale files: `src/locales/` (currently supports English)
- Use `$t()` function in templates and `t()` in script
- API field translations can be generated with `node scripts/getApiFields.js`

## Styling
- Vuetify 3 Material Design components
- SCSS configuration: `src/styles/settings.scss`
- Custom styles: `src/styles/`
- Theme and colors configured in `src/plugins/vuetify.js`


