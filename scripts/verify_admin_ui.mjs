// Real browser -> production Admin BFF -> API -> migrated PostgreSQL.
// All fixtures are synthetic and all browser requests stay on loopback.
import fs from 'node:fs/promises'
import assert from 'node:assert/strict'
import path from 'node:path'
const root = process.argv[2]
if (!root || !/^\/(private\/)?tmp\/reputation-approval-/.test(root)) throw new Error('Dedicated verification directory required')
const { chromium } = await import(path.join(root, 'browser/node_modules/playwright/index.mjs'))
const fixture = JSON.parse(await fs.readFile(path.join(root, 'ui-fixtures.json'), 'utf8'))
const base = `http://127.0.0.1:${fixture.ports.admin_port}`
const output = path.join(root, 'ui')
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
await context.route('**/*', route => {
  const url = new URL(route.request().url())
  return ['127.0.0.1', 'localhost'].includes(url.hostname) ? route.continue() : route.abort()
})
const page = await context.newPage()
page.setDefaultTimeout(15000)
const results = [], errors = [], serverErrors = [], inventories = []
page.on('pageerror', error => errors.push(error.message))
page.on('response', response => { if (response.status() >= 500) serverErrors.push({ url: response.url(), status: response.status() }) })
async function test(name, run) {
  try { await run(); results.push({ name, status: 'PASS' }); console.log('PASS', name) }
  catch (error) { results.push({ name, status: 'FAIL', url: page.url(), error: String(error) }); console.log('FAIL', name, String(error)); await page.screenshot({ path: path.join(output, `failure-${results.length}.png`), fullPage: true }) }
}
async function goto(url) { await page.goto(base + url); await page.waitForLoadState('networkidle'); await page.locator('main').waitFor(); await page.waitForTimeout(2500) }
const first = fixture.hospitals[0], second = fixture.hospitals[1]
await test('anonymous admin request is rejected', async () => {
  const response = await context.request.get(base + '/api/admin/hospitals', { maxRedirects: 0 })
  assert.ok([401, 403, 307].includes(response.status()))
})
await test('real login creates a server session', async () => {
  await goto('/login')
  await page.getByLabel('관리자 이메일').fill(fixture.email)
  await page.getByLabel('관리자 비밀번호').fill(fixture.password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await page.waitForURL('**/hospitals')
  await page.getByRole('heading', { name: '병원 목록', exact: true }).waitFor()
  const cookies = await context.cookies()
  assert.ok(cookies.some(cookie => cookie.name === 'admin_session' && cookie.httpOnly && cookie.secure))
})
if (!page.url().endsWith('/hospitals')) throw new Error('Login failed; refusing dependent mutations')
const routes = [
  ['hospitals', '/hospitals'], ['operations', '/operations'], ['leads', '/leads'],
  ['accounts', '/accounts'], ['new-contract', '/hospitals/new'],
  ['overview', `/hospitals/${first.id}`], ['info', `/hospitals/${first.id}/info`],
  ['content', `/hospitals/${first.id}/content`], ['reports', `/hospitals/${first.id}/reports`],
]
for (const [name, url] of routes) await test(`desktop ${name}: render without overflow`, async () => {
  await goto(url)
  assert.equal(await page.locator('html').evaluate(el => el.scrollWidth > innerWidth + 1), false)
  assert.ok((await page.locator('main').innerText()).trim().length > 15)
  assert.ok(!/확인하지 못했습니다|불러오지 못했습니다/.test(await page.locator('main').innerText()), 'unexpected load failure panel')
  inventories.push({ name, text: await page.locator('main').innerText(), inputs: await page.locator('input,textarea,select,button').evaluateAll(els => els.map(e => ({ tag: e.tagName, name: e.getAttribute('name'), id: e.id, type: e.getAttribute('type'), text: e.textContent?.trim().slice(0, 70) }))) })
  await page.screenshot({ path: path.join(output, `desktop-${name}.png`), fullPage: true })
})
const feedbackPath = `/api/admin/hospitals/${first.id}/director-feedback`
const topic = '배포 검증용 환자 설명 흐름'
async function readJSON(url) {
  return page.evaluate(async p => { const r = await fetch(p); if (!r.ok) throw Error(`HTTP ${r.status}`); return r.json() }, url)
}
await test('feedback save, duplicate suppression and reload persistence', async () => {
  await goto(`/hospitals/${first.id}/reports`)
  for (let attempt = 0; attempt < 2; attempt++) {
    await page.getByLabel('관심 주제 (한 줄에 하나)').fill(topic)
    const response = page.waitForResponse(r => r.url().endsWith('/director-feedback') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '대화 내용 저장' }).click()
    assert.ok((await response).ok())
    await page.getByRole('status').filter({ hasText: '대화 내용을 저장했습니다' }).waitFor()
  }
  await page.reload(); await page.waitForLoadState('networkidle')
  const rows = await readJSON(feedbackPath)
  assert.equal(rows.filter(r => r.prefer_topics.includes(topic)).length, 1)
  assert.equal(await page.getByLabel('관심 주제 (한 줄에 하나)').inputValue(), '')
})
await test('feedback remains hospital-scoped on navigation', async () => {
  await page.getByLabel('관심 주제 (한 줄에 하나)').fill('저장하지 않은 첫 병원 메모')
  await goto(`/hospitals/${second.id}/reports`)
  assert.equal(await page.getByLabel('관심 주제 (한 줄에 하나)').inputValue(), '')
  assert.equal((await readJSON(`/api/admin/hospitals/${second.id}/director-feedback`)).length, 0)
  assert.ok(!(await page.locator('main').innerText()).includes(topic))
})
await test('authenticated mutation without CSRF is refused', async () => {
  const status = await page.evaluate(async url => (await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source: 'DIRECTOR', prefer_topics: ['CSRF blocked'] }) })).status, feedbackPath)
  assert.equal(status, 403)
})
await test('explicit feedback retirement persists without generation', async () => {
  await goto(`/hospitals/${first.id}/reports`)
  await page.getByRole('button', { name: '적용 종료', exact: true }).click()
  await page.getByRole('status').filter({ hasText: '적용을 종료했습니다' }).waitFor()
  assert.equal((await readJSON(feedbackPath)).length, 0)
})
await test('schedule adjustment preserves all hospital-month identities', async () => {
  await goto(`/hospitals/${first.id}/content`)
  const before = await readJSON(`/api/admin/hospitals/${first.id}/content`)
  const details = page.locator('#content-schedule')
  if (!(await details.getAttribute('open'))) await details.locator('summary').click()
  const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' })
  const date = new Date(today + 'T12:00:00Z'); date.setUTCDate(date.getUTCDate() + 2)
  await page.getByLabel('시작일', { exact: true }).fill(date.toISOString().slice(0, 10))
  await page.getByRole('button', { name: '발행 일정 변경 확인', exact: true }).click()
  const saved = page.waitForResponse(r => r.url().endsWith('/schedule') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '원고를 보존하고 일정 저장', exact: true }).click()
  const response = await saved; assert.ok(response.ok(), await response.text())
  await page.getByText('발행 일정 설정 완료', { exact: true }).waitFor()
  const after = await readJSON(`/api/admin/hospitals/${first.id}/content`)
  assert.deepEqual(after.map(r => r.id).sort(), before.map(r => r.id).sort())
  assert.equal(after.length, 12)
  await page.screenshot({ path: path.join(output, 'schedule-saved.png'), fullPage: true })
})
await page.setViewportSize({ width: 390, height: 844 })
for (const [name, url] of routes) await test(`mobile ${name}: render without page overflow`, async () => {
  await goto(url)
  if (name === 'reports') {
    const copy = page.locator('[data-report-state-copy]').first()
    const box = await copy.boundingBox()
    const label = await copy.locator('span').first().boundingBox()
    assert.ok(box && box.width >= 140, 'report explanation must retain a readable column')
    assert.ok(label && label.height <= 28, 'status label must not stack one glyph per line')
  }
  assert.equal(await page.locator('html').evaluate(el => el.scrollWidth > innerWidth + 1), false)
  assert.ok(!/확인하지 못했습니다|불러오지 못했습니다/.test(await page.locator('main').innerText()), 'unexpected load failure panel')
  await page.screenshot({ path: path.join(output, `mobile-${name}.png`), fullPage: true })
})
await test('no browser runtime exceptions', async () => assert.deepEqual(errors, []))
await test('no same-origin HTTP 5xx during UI workflows', async () => assert.deepEqual(serverErrors, []))
await test('logout revokes browser access', async () => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await goto('/hospitals')
  await page.getByRole('button', { name: '로그아웃' }).click()
  await page.waitForURL(url => url.pathname === '/login')
  await page.goto(base + '/operations')
  await page.waitForURL(url => url.pathname === '/login')
})
await test('all workflows including logout have no HTTP 5xx', async () => assert.deepEqual(serverErrors, []))
await fs.writeFile(path.join(output, 'results.json'), JSON.stringify({ results, errors, serverErrors }, null, 2))
await fs.writeFile(path.join(output, 'inventories.json'), JSON.stringify(inventories, null, 2))
await browser.close()
console.log(JSON.stringify({ passed: results.filter(r => r.status === 'PASS').length, failed: results.filter(r => r.status === 'FAIL').length }))
if (results.some(result => result.status !== 'PASS')) process.exitCode = 1
