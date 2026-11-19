import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { chromium, expect } from '@playwright/test'

// Use the shipped Vue components, Pinia store and memory router. Requests can
// finish out of order, just as they can with two studies open in succession.
test('study pages bind to the selected study and version across late responses and retries', async (t) => {
  const root = fileURLToPath(new URL('../', import.meta.url))
  const harness = `
    import { createApp, h } from 'vue'
    import { createI18n } from 'vue-i18n'
    import { createRouter, createMemoryHistory } from 'vue-router'
    import { createPinia } from 'pinia'
    import PassThrough from '/src/components/layout/PassThrough.vue'
    import Overview from '/src/components/studies/StudyStructureOverview.vue'
    import { useStudiesGeneralStore } from '/src/stores/studies-general.js'
    import messages from '/src/locales/en/app.json'
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/:study_id', component: Overview, name: 'StudyStructure', meta: {studyRequired:true} },
      { path: '/:study_id/activities', component: Overview, name: 'StudyActivities', meta: {studyRequired:true} }
    ] })
    const app = createApp(PassThrough)
    app.use(createPinia()).use(router).use(createI18n({ legacy:false, locale:'en',messages:{en:messages} }))
    for(const tag of ['sheet','alert','btn','row','col','skeleton-loader']) {
      app.component('v-'+tag, { setup(_, {slots}) {return () => h(tag==='btn'?'button':'div',{},slots.default?.())} })
    }
    const store=useStudiesGeneralStore()
    window.fixture = {router, store, release:null, preferences:null, fail:false,
      async select(uid,version) {
        await store.selectStudy({uid,version_number:version})
        await router.replace('/'+uid)
      }
    }
    await fixture.select('A','1')
    app.mount('#app')
  `
  const api = `
    const response = data => Promise.resolve({data})
    export default {
      getStudy(uid,quiet,version) { fixture.versionRead = version; return new Promise(resolve => fixture.release = () => resolve({data:{uid,acronym:'stale'}})) },
      getSoAPreferredTimeUnit(uid) { return response(uid) },
      getStudyPreferredTimeUnit(uid) { return response(uid) },
      getSoAPreferences(uid) { return fixture.holdPreferences === uid ? new Promise(resolve => fixture.preferences = () => resolve({data:uid})) : response(uid) },
      getStudyElements() { return response({total:1}) },
      getStudyPopulationMetadata() { return response({current_metadata:{study_population:{}}}) },
      getAllForStudy(uid) { return response({items:[{arm_uid:uid,name:'Arm '+uid+' v'+fixture.store.selectedStudyVersion}]}) },
      getAllCohorts() { return fixture.fail ? Promise.reject(new Error('offline')) : response({items:[]}) },
      getStudyEpochs(uid) { return response({items:[{uid,epoch_name:'Epoch '+uid}]}) },
      getAllStudyCells() { return response([]) }, getSourceVariable() {return response({})},
      getStudyDesignClass() {return response({value:'Parallel'})}
    } `
  const server = await createServer({
    configFile: false,
    root,
    publicDir: false,
    optimizeDeps: {
      entries: [],
      include: ['vue', 'vue-router', 'pinia', 'vue-i18n'],
    },
    logLevel: 'error',
    appType: 'custom',
    server: { host: '127.0.0.1', port: 0, hmr: false },
    plugins: [
      {
        name: 'fixture',
        enforce: 'pre',
        resolveId(id, importer) {
          if (
            id === '/fixture.js' ||
            id.startsWith('@/api/') ||
            (id === './app' && importer?.includes('studies-general'))
          )
            return '\0fixture:' + id
          if (id.startsWith('@/'))
            return this.resolve('/src/' + id.slice(2), importer, {
              skipSelf: true,
            })
        },
        load(id) {
          if (id === '\0fixture:/fixture.js') return harness
          if (id === '\0fixture:./app')
            return 'export const useAppStore = () => ({syncStudyMenuParams(){}})'
          if (id.startsWith('\0fixture:@/api/')) return api
        },
        configureServer(s) {
          s.middlewares.use('/index.html', (_, res) => {
            res.setHeader('Content-Type', 'text/html')
            res.end(
              '<div id="app"></div><script type="module" src="/fixture.js"></script>'
            )
          })
        },
      },
      vue(),
    ],
  })
  t.after(() => server.close())
  await server.listen()
  const browser = await chromium.launch({
    channel: process.env.M11_BROWSER_CHANNEL,
  })
  t.after(() => browser.close())
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(server.resolvedUrls.local[0] + 'index.html')
  await expect(page.getByText('Arm A v1')).toBeVisible()
  await page.evaluate(() => {
    fixture.initializing = fixture.store.initialize()
  })
  await page.waitForFunction(() => !!fixture.release)
  assert.equal(await page.evaluate(() => fixture.versionRead), '1')
  await page.evaluate(async () => {
    fixture.holdPreferences = 'B'
    fixture.selectingB = fixture.store.selectStudy({
      uid: 'B',
      version_number: '1',
    })
  })
  await expect(page.getByText('Arm A v1')).toHaveCount(0)
  await page.evaluate(() => fixture.select('C', '2'))
  await expect(page.getByText('Arm C v2')).toBeVisible()
  await page.evaluate(async () => {
    fixture.preferences()
    fixture.release()
    await Promise.all([fixture.initializing, fixture.selectingB])
  })
  assert.deepEqual(
    await page.evaluate(() => [
      fixture.store.selectedStudy.uid,
      fixture.store.soaPreferences,
      JSON.parse(localStorage.selectedStudy).uid,
    ]),
    ['C', 'C', 'C']
  )
  await page.evaluate(() => fixture.select('C', '3'))
  await expect(page.getByText('Arm C v3')).toBeVisible()
  await expect(page.getByText('Arm C v2')).toHaveCount(0)
  await page.evaluate(() => {
    fixture.fail = true
    return fixture.select('D', null)
  })
  await expect(
    page.getByText('The study design table could not be loaded.')
  ).toBeVisible()
  await expect(page.getByText('Arm C v3')).toHaveCount(0)
  await page.evaluate(() => {
    fixture.fail = false
  })
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByText('Epoch D')).toBeVisible()
  assert.deepEqual(errors, [])
})
