import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { chromium, expect } from '@playwright/test'
import { compileScript, compileStyle, parse } from '@vue/compiler-sfc'

// Compile the production SFC and API module. Only the store and HTTP transport
// are substituted; rendering, watch cleanup, Vuetify, and iframe isolation run
// in an actual browser without building the full application or contacting APIs.
const require = createRequire(import.meta.url)
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const translations = JSON.parse(await read('../src/locales/en/app.json'))
const { descriptor, errors } = parse(
  await read('../src/views/studies/IchM11Page.vue'),
  { filename: 'IchM11Page.vue' }
)
assert.deepEqual(errors, [])
const component = compileScript(descriptor, {
  id: 'm11-page',
  inlineTemplate: true,
}).content
const style = compileStyle({
  filename: 'IchM11Page.vue',
  source: descriptor.styles[0].content,
  id: 'data-v-m11-page',
  scoped: true,
})
assert.deepEqual(style.errors, [])

const routes = new Map([
  ['/component.js', ['text/javascript', component]],
  ['/component.css', ['text/css', style.code]],
  [
    '/vue.js',
    [
      'text/javascript',
      await readFile(require.resolve('vue/dist/vue.esm-browser.js'), 'utf8'),
    ],
  ],
  [
    '/vuetify.js',
    [
      'text/javascript',
      await readFile(require.resolve('vuetify/dist/vuetify.esm.js'), 'utf8'),
    ],
  ],
  [
    '/vuetify.css',
    [
      'text/css',
      await readFile(require.resolve('vuetify/dist/vuetify.css'), 'utf8'),
    ],
  ],
  ['/api/study.js', ['text/javascript', await read('../src/api/study.js')]],
  [
    '/constants.js',
    ['text/javascript', await read('../src/constants/study.js')],
  ],
  [
    '/store.js',
    [
      'text/javascript',
      `import { reactive } from 'vue'
       export const store = reactive({ selectedStudy: null, selectedStudyVersion: null })
       export const useStudiesGeneralStore = () => store`,
    ],
  ],
  [
    '/api/repository',
    [
      'text/javascript',
      `export default {
         get(url, config) {
           return new Promise((resolve, reject) => {
             // Deliberately ignore abort: late transport completion must not
             // overwrite the currently selected preview or affect a remount.
             window.m11Test.requests.push({ url, config, resolve, reject })
           })
         }
       }`,
    ],
  ],
  [
    '/harness.js',
    [
      'text/javascript',
      `import { createApp } from 'vue'
       import { createVuetify, components, directives } from '/vuetify.js'
       import IchM11Page from '/component.js'
       import { store } from '/store.js'
       const messages = ${JSON.stringify(translations)}
       window.m11Test = {
         requests: [],
         select(uid, version) {
           store.selectedStudy = uid ? { uid } : null
           store.selectedStudyVersion = version
         },
         resolve(index, data) { this.requests[index].resolve({ data }) },
         reject(index) { this.requests[index].reject(new Error('Synthetic request failure')) },
         mount() {
           IchM11Page.__scopeId = 'data-v-m11-page'
           const app = createApp(IchM11Page)
           app.config.globalProperties.$t = (key) => key.split('.').reduce((v, p) => v[p], messages)
           app.use(createVuetify({ components, directives }))
           app.mount('#app')
           this.unmount = () => app.unmount()
         }
       }
       window.m11Test.mount()`,
    ],
  ],
  [
    '/',
    [
      'text/html',
      `<!doctype html><html><head>
       <meta charset="utf-8"><link rel="stylesheet" href="/vuetify.css">
       <link rel="stylesheet" href="/component.css">
       <script type="importmap">${JSON.stringify({
         imports: {
           vue: '/vue.js',
           '@/stores/studies-general': '/store.js',
           '@/api/study': '/api/study.js',
           '@/constants/study': '/constants.js',
         },
       })}</script></head><body>
       <iframe id="unrelated-frame" title="Unrelated frame" srcdoc="Unrelated content"></iframe>
       <div id="app" style="min-height: 650px"></div>
       <script type="module" src="/harness.js"></script></body></html>`,
    ],
  ],
])

let server
let browser
let baseUrl
before(async () => {
  server = createServer((request, response) => {
    const route = routes.get(request.url)
    response.writeHead(route ? 200 : 404, {
      'Content-Type': route?.[0] ?? 'text/plain',
    })
    response.end(route?.[1] ?? 'Not found')
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  baseUrl = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({
    headless: true,
    ...(process.env.M11_BROWSER_CHANNEL
      ? { channel: process.env.M11_BROWSER_CHANNEL }
      : {}),
  })
})
after(async () => {
  await browser?.close()
  await new Promise((resolve) => server?.close(resolve) ?? resolve())
})

async function openPage(t) {
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } })
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  t.after(async () => {
    await page.close()
    assert.deepEqual(errors, [], 'no unhandled browser errors')
  })
  await page.goto(baseUrl)
  await expect(
    page.getByText(translations._global.no_study_selected)
  ).toBeVisible()
  return page
}

async function select(page, uid, version, requestCount) {
  await page.evaluate(
    ([uid, version]) => window.m11Test.select(uid, version),
    [uid, version]
  )
  await expect
    .poll(() => page.evaluate(() => window.m11Test.requests.length))
    .toBe(requestCount)
}

const html = (label) => `<html><body><h1>${label}</h1></body></html>`
const preview = (page) => page.locator('#app iframe')

test('loads the explicit version through the real API method and isolates script-capable HTML', async (t) => {
  const page = await openPage(t)
  await select(page, 'Study_1', '2.0', 1)
  await expect(page.getByText(translations.IchM11Page.loading)).toBeVisible()
  await expect(preview(page)).toHaveCount(0)
  assert.deepEqual(
    await page.evaluate(() => {
      const { url, config } = window.m11Test.requests[0]
      return { url, params: config.params, ignoreErrors: config.ignoreErrors }
    }),
    {
      url: 'usdm/v4/studyDefinitions/Study_1/m11',
      params: { study_value_version: '2.0' },
      ignoreErrors: true,
    }
  )
  await page.evaluate(() => {
    window.m11Test.resolve(
      0,
      `<h1>Selected protocol 2.0</h1><button>Open detail</button>
       <p id="detail" hidden>Protocol detail</p>
       <script>
         document.querySelector('button').onclick = () => document.querySelector('#detail').hidden = false
         try { parent.document.body.dataset.escaped = 'yes' }
         catch { document.body.dataset.parentBlocked = 'yes' }
       </script>`
    )
  })
  await expect(preview(page)).toHaveAttribute(
    'title',
    translations.IchM11Page.title
  )
  await expect(preview(page)).toHaveAttribute('sandbox', 'allow-scripts')
  const frame = preview(page).contentFrame()
  await expect(frame.getByRole('heading')).toHaveText('Selected protocol 2.0')
  await frame.getByRole('button', { name: 'Open detail' }).click()
  await expect(frame.getByText('Protocol detail')).toBeVisible()
  await expect(frame.locator('body')).toHaveAttribute(
    'data-parent-blocked',
    'yes'
  )
  await expect(page.locator('body')).not.toHaveAttribute('data-escaped')
  await expect(page.locator('#unrelated-frame')).toHaveAttribute(
    'srcdoc',
    'Unrelated content'
  )
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  if (process.env.M11_PREVIEW_SCREENSHOT) {
    await page.screenshot({
      path: process.env.M11_PREVIEW_SCREENSHOT,
      fullPage: true,
    })
  }
})

test('study and version changes clear old HTML and ignore out-of-order successes and failures', async (t) => {
  const page = await openPage(t)
  await select(page, 'Study_1', '1.0', 1)
  await page.evaluate(
    (data) => window.m11Test.resolve(0, data),
    html('Version 1')
  )
  await expect(preview(page).contentFrame().getByRole('heading')).toHaveText(
    'Version 1'
  )
  await select(page, 'Study_1', '2.0', 2)
  await expect(preview(page)).toHaveCount(0)
  await select(page, 'Study_2', '3.0', 3)
  assert.equal(
    await page.evaluate(() => window.m11Test.requests[1].config.signal.aborted),
    true
  )
  await page.evaluate(
    (data) => window.m11Test.resolve(2, data),
    html('Study 2 version 3')
  )
  await page.evaluate(
    (data) => window.m11Test.resolve(1, data),
    html('Stale version 2')
  )
  await expect(preview(page).contentFrame().getByRole('heading')).toHaveText(
    'Study 2 version 3'
  )
  await select(page, 'Study_2', '4.0', 4)
  await select(page, 'Study_2', '5.0', 5)
  await page.evaluate(() => window.m11Test.reject(3))
  await expect(page.getByText(translations.IchM11Page.loading)).toBeVisible()
  await expect(page.getByText(translations.IchM11Page.load_error)).toHaveCount(
    0
  )
  await page.evaluate(
    (data) => window.m11Test.resolve(4, data),
    html('Version 5')
  )
  await expect(preview(page).contentFrame().getByRole('heading')).toHaveText(
    'Version 5'
  )
})

test('failure clears stale output, ends loading and retries the currently selected version', async (t) => {
  const page = await openPage(t)
  await select(page, 'Study_1', null, 1)
  assert.equal(
    await page.evaluate(
      () => window.m11Test.requests[0].config.params.study_value_version
    ),
    null
  )
  await page.evaluate(
    (data) => window.m11Test.resolve(0, data),
    html('Current draft')
  )
  await expect(preview(page)).toHaveCount(1)
  await select(page, 'Study_1', '2.0', 2)
  await page.evaluate(() => window.m11Test.reject(1))
  await expect(page.getByRole('alert')).toContainText(
    translations.IchM11Page.load_error
  )
  await expect(page.getByText(translations.IchM11Page.loading)).toHaveCount(0)
  await expect(preview(page)).toHaveCount(0)
  await page
    .getByRole('button', { name: translations.IchM11Page.retry })
    .click()
  await expect
    .poll(() => page.evaluate(() => window.m11Test.requests.length))
    .toBe(3)
  assert.equal(
    await page.evaluate(
      () => window.m11Test.requests[2].config.params.study_value_version
    ),
    '2.0'
  )
  await expect(page.getByText(translations.IchM11Page.load_error)).toHaveCount(
    0
  )
  await page.evaluate(
    (data) => window.m11Test.resolve(2, data),
    html('Retried 2.0')
  )
  await expect(preview(page).contentFrame().getByRole('heading')).toHaveText(
    'Retried 2.0'
  )
})

test('empty and malformed payloads remain explicit and clearing selection stops pending output', async (t) => {
  const page = await openPage(t)
  await select(page, 'Study_1', '1.0', 1)
  await page.evaluate(() => window.m11Test.resolve(0, '  '))
  await expect(page.getByText(translations.IchM11Page.empty)).toBeVisible()
  await expect(preview(page)).toHaveCount(0)
  await select(page, 'Study_1', '2.0', 2)
  await page.evaluate(() => window.m11Test.resolve(1, { unexpected: 'json' }))
  await expect(page.getByText(translations.IchM11Page.load_error)).toBeVisible()
  await select(page, 'Study_1', '3.0', 3)
  await select(page, null, null, 3)
  await expect(
    page.getByText(translations._global.no_study_selected)
  ).toBeVisible()
  await page.evaluate(
    (data) => window.m11Test.resolve(2, data),
    html('No longer selected')
  )
  await expect(preview(page)).toHaveCount(0)
  assert.equal(
    await page.evaluate(() => window.m11Test.requests[2].config.signal.aborted),
    true
  )
})

test('unmount aborts pending work and late completion cannot overwrite a new component', async (t) => {
  const page = await openPage(t)
  await select(page, 'Study_1', '1.0', 1)
  await page.evaluate(() => {
    window.m11Test.unmount()
    window.m11Test.select('Study_2', '2.0')
    window.m11Test.mount()
  })
  await expect
    .poll(() => page.evaluate(() => window.m11Test.requests.length))
    .toBe(2)
  assert.equal(
    await page.evaluate(() => window.m11Test.requests[0].config.signal.aborted),
    true
  )
  await page.evaluate(
    (data) => window.m11Test.resolve(1, data),
    html('New component')
  )
  await page.evaluate(
    (data) => window.m11Test.resolve(0, data),
    html('Unmounted component')
  )
  await expect(preview(page).contentFrame().getByRole('heading')).toHaveText(
    'New component'
  )
})
