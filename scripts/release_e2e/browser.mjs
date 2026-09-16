// Actual production Admin image -> BFF -> API -> migrated DB -> rendered PDF bytes.
import fs from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
const root = process.argv[2]
assert.ok(root?.startsWith('/private/tmp/reputation-approval-'))
const { chromium } = await import(path.join(root, '../browser/node_modules/playwright/index.mjs'))
const fixture = JSON.parse(await fs.readFile(path.join(root, 'ui-fixtures.json'), 'utf8'))
const happy = JSON.parse(await fs.readFile(path.join(root, 'happy-report.json'), 'utf8'))
const base = 'http://127.0.0.1:53186'
const resource = `/api/admin/hospitals/${happy.hospital_id}/reports/${happy.report_id}`
const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
await context.route('**/*', r => ['127.0.0.1','localhost'].includes(new URL(r.request().url()).hostname) ? r.continue() : r.abort())
const page = await context.newPage(); page.setDefaultTimeout(20000)
page.on('dialog', dialog => dialog.accept())
const results = [], errors = [], serverErrors = []
let baselineHistory = []
page.on('pageerror', e => errors.push(e.message))
page.on('response', r => { if (r.status() >= 500) serverErrors.push({ url: r.url(), status: r.status() }) })
async function check(name, fn) { await fn(); results.push({ name, status:'PASS' }); console.log('PASS', name) }
async function read() { return page.evaluate(async p => { const r = await fetch(p); if (!r.ok) throw Error(`HTTP ${r.status}`); return r.json() }, resource) }
async function action(button) { const result = page.waitForResponse(r => r.request().method() === 'POST' && r.url().includes(happy.report_id)); await button.click(); const r = await result; assert.equal(r.status(), 200, await r.text()) }
const dialog = page.getByRole('dialog')
async function download() { const result = page.waitForResponse(r => r.url().includes('/download?audience=doctor')); await dialog.getByRole('button', {name:'원장 전달용 보고서 열기',exact:true}).click(); const r = await result; assert.equal(r.status(),200); assert.equal(createHash('sha256').update(await r.body()).digest('hex'),happy.sha256); await dialog.getByText(/내려받은 파일 확인 번호/).waitFor() }
try {
  await check('production login and real session', async () => {
    await page.goto(base+'/login'); await page.getByLabel('관리자 이메일').fill(fixture.email)
    await page.getByLabel('관리자 비밀번호').fill(fixture.password)
    await page.getByRole('button',{name:'로그인',exact:true}).click(); await page.waitForURL('**/hospitals')
    assert.ok((await context.cookies()).some(c=>c.name==='admin_session' && c.secure && c.httpOnly))
  })
  await check('worker-built monthly report is ready in the real dialog', async () => {
    assert.equal((await read()).delivery_ready,true)
    await page.goto(`${base}/hospitals/${happy.hospital_id}/reports`)
    await page.getByRole('button',{name:'열기',exact:true}).click(); await dialog.waitFor()
    // Rehearsals are repeatable without deleting append-only delivery history.
    const previous = await read()
    if (previous.effective_delivery && previous.effective_delivery.event_type !== 'RESCINDED') {
      await dialog.getByRole('button',{name:'전달 기록 무효 처리',exact:true}).click()
      await dialog.getByLabel('무효 처리 이유').fill('격리 검증의 다음 독립 전달 사이클 준비')
      await action(dialog.getByRole('button',{name:'이유를 남기고 기록 무효 처리'}))
      await dialog.getByText('현재 유효한 전달 기록').waitFor({state:'detached'})
    }
    baselineHistory = (await read()).delivery_history
    assert.ok(await dialog.getByRole('button',{name:'먼저 원장 전달용 파일을 여기서 내려받아야 합니다'}).isDisabled())
  })
  await check('forged PDF hash and missing CSRF cannot create delivery', async () => {
    const status = await page.evaluate(async p => {
      const payload=JSON.stringify({artifact_sha256:'0'.repeat(64),recipient_label:'검증',channel:'대면'})
      const csrf=decodeURIComponent(document.cookie.split('; ').find(s=>s.startsWith('admin_csrf=')).split('=').slice(1).join('='))
      const missing=await fetch(p+'/mark-sent',{method:'POST',headers:{'Content-Type':'application/json'},body:payload})
      const forged=await fetch(p+'/mark-sent',{method:'POST',headers:{'Content-Type':'application/json','X-Admin-CSRF-Token':csrf},body:payload})
      return [missing.status,forged.status]
    },resource)
    assert.deepEqual(status,[403,409]); assert.equal((await read()).delivery_history.length,baselineHistory.length)
  })
  await check('downloaded PDF hash matches the actual validated artifact',download)
  await check('record delivery through the dialog',async()=>{
    await dialog.getByLabel('받은 분').fill('검증 원장'); await dialog.getByLabel('전달 방법').fill('대면')
    await action(dialog.getByRole('button',{name:'이 파일의 원장 전달 기록 남기기'}))
    await dialog.getByText('현재 유효한 전달 기록').waitFor()
    assert.equal((await read()).effective_delivery.event_type,baselineHistory.length ? 'REDELIVERED' : 'DELIVERED')
  })
  await check('correction appends without deleting the original',async()=>{
    await dialog.getByRole('button',{name:'전달 정보 수정 기록 추가'}).click()
    await dialog.getByLabel('받은 분').fill('검증 원장 수정'); await dialog.getByLabel('수정 이유').fill('격리 검증 수신자 정정')
    await action(dialog.getByRole('button',{name:'수정 기록 추가',exact:true}))
    assert.equal((await read()).effective_delivery.event_type,'CORRECTED')
  })
  await check('rescission preserves history and clears effective delivery',async()=>{
    await dialog.getByRole('button',{name:'전달 기록 무효 처리',exact:true}).click()
    await dialog.getByLabel('무효 처리 이유').fill('격리 E2E 무효 처리 검증')
    await action(dialog.getByRole('button',{name:'이유를 남기고 기록 무효 처리'}))
    await dialog.getByText('현재 유효한 전달 기록').waitFor({state:'detached'})
    assert.equal((await read()).effective_delivery.event_type,'RESCINDED')
  })
  await check('re-download and re-delivery retain the same byte identity',async()=>{
    await download(); await dialog.getByLabel('받은 분').fill('검증 원장')
    await action(dialog.getByRole('button',{name:'이 파일의 원장 전달 기록 남기기'}))
    await dialog.getByText('현재 유효한 전달 기록').waitFor()
    assert.equal((await read()).effective_delivery.event_type,'REDELIVERED')
  })
  await check('history survives reload and retains all four hash-bound events', async () => {
    await page.reload(); await page.waitForLoadState('networkidle')
    const report = await read()
    const priorIds = new Set(baselineHistory.map(e => e.id))
    const fresh = report.delivery_history.filter(e => !priorIds.has(e.id))
    assert.equal(report.delivery_history.length, baselineHistory.length + 4)
    assert.deepEqual(fresh.map(e => e.event_type).sort(), ['CORRECTED',baselineHistory.length ? 'REDELIVERED' : 'DELIVERED','REDELIVERED','RESCINDED'].sort())
    assert.ok(baselineHistory.every(e => report.delivery_history.some(after => after.id === e.id)))
    assert.ok(report.delivery_history.every(e => e.artifact_sha256 === happy.sha256))
    assert.equal(report.effective_delivery.event_type, 'REDELIVERED')
    await page.getByRole('button', {name:'열기',exact:true}).click(); await dialog.waitFor()
  })
  await check('another hospital cannot read the report', async () => {
    const other = fixture.hospitals.find(h => h.id !== happy.hospital_id)
    // Chrome's secure loopback cookie semantics, not APIRequestContext's HTTP jar.
    const status = await page.evaluate(async p => (await fetch(p)).status,
      `/api/admin/hospitals/${other.id}/reports/${happy.report_id}`)
    assert.equal(status, 404)
  })
  await check('desktop and mobile dialog remain usable', async () => {
    await page.screenshot({path:path.join(root,'report-delivery-desktop.png'),fullPage:true})
    await page.setViewportSize({width:390,height:844})
    assert.equal(await page.locator('html').evaluate(e => e.scrollWidth > innerWidth + 1),false)
    await page.screenshot({path:path.join(root,'report-delivery-mobile.png'),fullPage:true})
  })
  await check('no runtime exception or server 5xx', async () => {
    assert.deepEqual(errors, []); assert.deepEqual(serverErrors, [])
  })
} catch (error) {
  results.push({name:'workflow',status:'FAIL',error:String(error),url:page.url()})
  await page.screenshot({path:path.join(root,'report-delivery-failure.png'),fullPage:true})
  process.exitCode=1
} finally {
  await fs.writeFile(path.join(root,'browser-results.json'),JSON.stringify({results,errors,serverErrors},null,2))
  await browser.close()
}
