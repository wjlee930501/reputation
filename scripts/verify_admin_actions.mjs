// Stateful UI checks against the dedicated release_ui database only.
import fs from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
const root = process.argv[2]
if (!root || !/^\/(private\/)?tmp\/reputation-approval-/.test(root)) throw Error('Isolated release directory required')
const { chromium } = await import(path.join(root, 'browser/node_modules/playwright/index.mjs'))
const fixture = JSON.parse(await fs.readFile(path.join(root, 'ui-fixtures.json'), 'utf8'))
const base = `http://127.0.0.1:${fixture.ports.admin_port}`
const browser = await chromium.launch({ headless: true, executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
await context.route('**/*', r => ['127.0.0.1', 'localhost'].includes(new URL(r.request().url()).hostname) ? r.continue() : r.abort())
const page = await context.newPage()
page.setDefaultTimeout(15000)
page.on('dialog', dialog => dialog.accept())
const results = [], errors = []
page.on('pageerror', e => errors.push(e.message))
async function check(name, run) {
  try { await run(); results.push({ name, status: 'PASS' }); console.log('PASS', name) }
  catch (e) { results.push({ name, status: 'FAIL', url: page.url(), error: String(e) }); console.log('FAIL', name, String(e)); await page.screenshot({ path: path.join(root, 'ui', `action-failure-${results.length}.png`), fullPage: true }) }
}
async function goto(p) { await page.goto(base + p); await page.waitForLoadState('networkidle') }
async function get(p) { return page.evaluate(async p => { const r = await fetch(p); if (!r.ok) throw Error(`HTTP ${r.status}`); return r.json() }, p) }
await goto('/login')
await page.getByLabel('관리자 이메일').fill(fixture.email)
await page.getByLabel('관리자 비밀번호').fill(fixture.password)
await page.getByRole('button', { name: '로그인', exact: true }).click()
await page.waitForURL('**/hospitals')
const hospital = fixture.hospitals[0]
await check('pause UI changes both Admin state and public API eligibility', async () => {
  await goto(`/hospitals/${hospital.id}`)
  const response = page.waitForResponse(r => r.url().endsWith('/pause') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '일시정지', exact: true }).click()
  assert.equal((await response).status(), 200)
  await page.getByRole('button', { name: '재개', exact: true }).waitFor()
  assert.equal((await get(`/api/admin/hospitals/${hospital.id}`)).status, 'PAUSED')
  const publicResponse = await context.request.get(`http://127.0.0.1:${fixture.ports.api_port}/api/v1/public/hospitals/${hospital.slug}`)
  assert.equal(publicResponse.status(), 404)
  await page.screenshot({ path: path.join(root, 'ui', 'paused-hospital.png'), fullPage: true })
})
await check('resume UI restores the guarded public API state', async () => {
  const response = page.waitForResponse(r => r.url().endsWith('/resume') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '재개', exact: true }).click()
  assert.equal((await response).status(), 200)
  await page.getByRole('button', { name: '일시정지', exact: true }).waitFor()
  assert.equal((await get(`/api/admin/hospitals/${hospital.id}`)).status, 'ACTIVE')
  assert.equal((await context.request.get(`http://127.0.0.1:${fixture.ports.api_port}/api/v1/public/hospitals/${hospital.slug}`)).status(), 200)
})
await check('blocked report cannot be recorded as delivered through its dialog', async () => {
  await goto(`/hospitals/${hospital.id}/reports`)
  await page.getByRole('button', { name: '열기', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.waitFor()
  assert.ok(await dialog.getByRole('button', { name: '차단 항목을 해결한 뒤 기록할 수 있습니다' }).isDisabled())
  assert.equal(await dialog.getByRole('button', { name: '원장 전달용 보고서 열기', exact: true }).count(), 0)
  await page.screenshot({ path: path.join(root, 'ui', 'blocked-report-dialog.png'), fullPage: true })
  await dialog.getByRole('button', { name: '보고서 닫기' }).click()
})
await check('stateful workflows produce no browser exceptions', async () => assert.deepEqual(errors, []))
await fs.writeFile(path.join(root, 'ui', 'actions-results.json'), JSON.stringify({ results, errors }, null, 2))
await browser.close()
console.log(JSON.stringify({ passed: results.filter(r => r.status === 'PASS').length, failed: results.filter(r => r.status === 'FAIL').length }))
if (results.some(r => r.status !== 'PASS')) process.exitCode = 1
