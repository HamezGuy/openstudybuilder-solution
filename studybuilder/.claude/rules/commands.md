# Development Commands

## Start Development Server
```bash
yarn dev
```
Starts the Vite dev server with hot-reload at http://localhost:5173 (default).

## Build for Production
```bash
yarn build              # Standard build
yarn build:dev          # Build with dev mode
yarn build:staging      # Build with staging mode
```

## Testing
```bash
yarn test:unit          # Run Jest unit tests with coverage
```
- Tests are in `tests/` directory
- Jest config: `jest.config.js`
- Coverage is collected from `src/` (excluding locales and plugins)

## Code Quality
```bash
yarn lint               # Run ESLint with auto-fix
yarn lint:staged        # Lint only staged files (used by pre-commit hooks)
yarn format             # Run Prettier on all files
yarn format:staged      # Format only staged files
```

## Other Utilities
```bash
node scripts/getApiFields.js    # Extract API field translations from OpenAPI spec
yarn build-sbom                 # Generate software bill of materials
```


