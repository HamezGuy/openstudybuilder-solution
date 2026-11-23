import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { chromium, expect } from '@playwright/test'

const root = fileURLToPath(new URL('../', import.meta.url))
const study = 'Study_000001'
const element = 'StudyElement_000001'
const selection = '/fixture-api/studies/' + study + '/study-elements/' + element

// Render the shipped element form. The stepper exposes its normal validated
// details step, and the selected study remains fixed throughout this fixture.
const modules = {
  page: `
    import { createApp, h } from 'vue'
    import { createI18n } from 'vue-i18n'
    import Form from '/src/components/studies/StudyElementsForm.vue'
    import { createVuetify } from 'vuetify'
    import { VAlert, VBtn, VTextField, VTextarea, VAutocomplete, VForm, VRow, VCol, VSpacer, VColorPicker, VSelect, VMessages } from 'vuetify/components'
    import messages from '/src/locales/en/app.json'

    const app = createApp({
      render() {
        const editing = !new URLSearchParams(location.search).has('create')
        return h(Form, { editedElement: editing ? { element_uid: '${element}' } : {}, onClose() { window.editorClosed = true } })
      },
    })
    app.use(createI18n({
      legacy: false, locale: 'en', missingWarn: false, fallbackWarn: false,
      messages: { en: messages },
    }))
    app.use(createVuetify({ components: { VAlert, VBtn, VTextField, VTextarea, VAutocomplete, VForm, VRow, VCol, VSpacer, VColorPicker, VSelect, VMessages } }))
    app.provide('notificationHub', { clearErrors() {}, add() {} })
    app.provide('formRules', { required: () => true, max: () => true, min_value: () => true, max_value: () => true })
    app.mount('#app')
  `,
  stepper: `
    import { h, ref } from 'vue'
    export default {
      setup(_, { slots, expose }) {
        expose({ currentStep: ref(1), validateStepObserver: async () => true })
        return () => h('section', [slots['step.details'](), slots.customActions()])
      },
    }
  `,
  store: `export const useStudiesGeneralStore = () => ({ selectedStudy: { uid: '${study}' }, units: [{uid: 'UnitDefinition_000001', name: 'days'}] })`,
  emptyComponent: 'export default { render() { return null } }',
  studyList: 'export default { getAllList: async () => ({data: []}) }',
  repository:
    "import axios from 'axios'; export default axios.create({baseURL: '/fixture-api/'})",
}

test('saving a native element preserves the draft across a stale revision and updates only the reviewed fields', async (t) => {
  const fixtures = new Map([
    ['/fixture.js', 'page'],
    ['@/stores/studies-general', 'store'],
    ['@/api/study', 'studyList'],
    ['@/components/tools/HorizontalStepperForm.vue', 'stepper'],
    ['@/components/studies/StudySelectorField.vue', 'emptyComponent'],
    ['@/components/tools/ConfirmDialog.vue', 'emptyComponent'],
  ])
  const prefix = '\0element-test:'
  const cacheDir = await mkdtemp(join(tmpdir(), 'osb-element-test-'))
  const server = await createServer({
    configFile: false,
    root,
    cacheDir,
    publicDir: false,
    appType: 'custom',
    logLevel: 'error',
    server: { host: '127.0.0.1', port: 0, hmr: false },
    optimizeDeps: {
      entries: [],
      include: [
        'vue',
        'vue-i18n',
        'vuetify',
        'vuetify/components',
        'axios',
        'lodash/isEqual',
      ],
    },
    plugins: [
      {
        name: 'native-element-fixture',
        enforce: 'pre',
        resolveId(id, importer) {
          if (fixtures.has(id)) return prefix + fixtures.get(id)
          if (id === './repository' && importer?.endsWith('/src/api/arms.js'))
            return prefix + 'repository'
          if (id === '@/api/arms')
            return this.resolve('/src/api/arms.js', importer, {
              skipSelf: true,
            })
          if (id === '@/components/tools/DurationField.vue')
            return this.resolve(
              '/src/components/tools/DurationField.vue',
              importer,
              { skipSelf: true }
            )
        },
        load(id) {
          if (id.startsWith(prefix)) return modules[id.slice(prefix.length)]
        },
        configureServer(vite) {
          vite.middlewares.use('/index.html', (_, response) => {
            response.setHeader('Content-Type', 'text/html')
            response.end(
              '<div id="app"></div><script type="module" src="/fixture.js"></script>'
            )
          })
        },
      },
      vue(),
    ],
  })
  t.after(async () => {
    await server.close()
    await rm(cacheDir, { recursive: true, force: true })
  })
  await server.listen()
  const browser = await chromium.launch({
    channel: process.env.M11_BROWSER_CHANNEL,
  })
  t.after(() => browser.close())
  const page = await browser.newPage()
  const jsErrors = []
  page.on('pageerror', (error) => jsErrors.push(error.message))
  const unit = { uid: 'UnitDefinition_000001', name: 'days' }
  const original = {
    study_uid: study,
    element_uid: element,
    name: 'Treatment period',
    short_name: 'TP',
    description: 'Original period',
    planned_duration: { duration_value: 5, duration_unit_code: unit },
    accepted_version: false,
    edit_revision: '2026-11-01T10:00:00.123456Z',
  }
  let current = original
  let refuseOneReload = false
  const newerRevision = '2026-11-01T10:00:00.123457Z'
  const requests = []
  await page.route('**/fixture-api/**', async (route) => {
    const method = route.request().method()
    const pathname = new URL(route.request().url()).pathname
    if (
      method === 'GET' &&
      pathname === '/fixture-api/study-elements/allowed-element-configs'
    )
      return route.fulfill({ json: [] })
    if (method === 'GET' && pathname === selection) {
      if (refuseOneReload) {
        refuseOneReload = false
        return route.fulfill({ status: 503, json: {} })
      }
      return route.fulfill({ json: current })
    }
    requests.push({ method, pathname, body: route.request().postDataJSON() })
    if (pathname === selection && method === 'PATCH' && requests.length === 1) {
      current = {
        ...original,
        short_name: 'TP2',
        description: 'Already revised by another editor',
        planned_duration: { duration_value: 6, duration_unit_code: unit },
        edit_revision: newerRevision,
      }
      return route.fulfill({
        status: 409,
        json: {
          code: 'STUDY_ELEMENT_VERSION_CONFLICT',
          details: [{ ctx: { current_start_date: newerRevision } }],
        },
      })
    }
    return route.fulfill({
      status:
        (pathname === selection && method === 'PATCH') ||
        (pathname === selection.slice(0, selection.lastIndexOf('/')) &&
          method === 'POST')
          ? 200
          : 404,
      json: {},
    })
  })
  await page.goto(server.resolvedUrls.local[0] + 'index.html')
  const description = page.getByRole('textbox', {
    name: 'Description',
    exact: true,
  })
  await expect(description).toHaveValue(original.description)
  await description.fill('Revised period from browser')
  const duration = page.locator('[data-cy="duration-value"] input')
  await duration.fill('0')
  const save = page.getByRole('button', { name: 'Save', exact: true })
  await save.click()
  await expect(
    page.getByText('Your edits are still in the form.', { exact: false })
  ).toBeVisible()
  await expect(save).toBeDisabled()
  await expect(description).toHaveValue('Revised period from browser')
  assert.deepEqual(requests, [
    {
      method: 'PATCH',
      pathname: selection,
      body: {
        description: 'Revised period from browser',
        planned_duration: { duration_value: 0, duration_unit_code: unit },
        expected_start_date: original.edit_revision,
      },
    },
  ])
  const review = page.getByRole('button', { name: 'Review current version' })
  refuseOneReload = true
  const failedRead = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === selection &&
      response.status() === 503
  )
  await review.click()
  await failedRead
  await expect(description).toHaveValue('Revised period from browser')
  await expect(save).toBeDisabled()
  await review.click()
  await expect(
    page.getByText(current.description, { exact: true })
  ).toBeVisible()
  await expect(description).toHaveValue('Revised period from browser')
  await expect(duration).toHaveValue('0')
  await expect(page.getByText('6 days', { exact: true })).toBeVisible()
  await expect(
    page.getByRole('textbox', { name: 'Element short name' })
  ).toHaveValue('TP2')
  assert.equal(
    requests.length,
    1,
    'reviewing the current data never retries the write'
  )
  await save.click()
  await page.waitForFunction(() => window.editorClosed === true)
  assert.equal(requests.length, 2)
  assert.deepEqual(requests[1], {
    method: 'PATCH',
    pathname: selection,
    body: {
      description: 'Revised period from browser',
      planned_duration: { duration_value: 0, duration_unit_code: unit },
      expected_start_date: newerRevision,
    },
  })
  current = { ...original, edit_revision: null, accepted_version: true }
  await page.reload()
  await expect(description).toHaveValue(original.description)
  await expect(save).toBeDisabled()
  await expect(
    page.getByText('A current draft revision is required to edit this element.')
  ).toBeVisible()
  assert.equal(
    requests.length,
    2,
    'an approval flag alone cannot authorize an edit'
  )
  // A persisted numeric zero and a cleared duration are different edits.
  current = {
    ...original,
    planned_duration: { duration_value: 0, duration_unit_code: unit },
  }
  await page.reload()
  await expect(duration).toHaveValue('0')
  await duration.fill('')
  await save.click()
  await page.waitForFunction(() => window.editorClosed === true)
  assert.deepEqual(requests.at(-1).body, {
    planned_duration: null,
    expected_start_date: original.edit_revision,
  })
  // The same native payload builder must preserve the zero typed on create.
  await page.goto(server.resolvedUrls.local[0] + 'index.html?create')
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await duration.fill('0')
  await page.locator('[data-cy="duration-unit"]').click()
  await page.getByRole('option', { name: 'days' }).click()
  await save.click()
  await page.waitForFunction(() => window.editorClosed === true)
  assert.equal(requests.at(-1).method, 'POST')
  assert.deepEqual(requests.at(-1).body.planned_duration, {
    duration_value: 0,
    duration_unit_code: unit,
  })
  assert.deepEqual(jsErrors, [])
})
