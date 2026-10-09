import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { chromium } from '/runner/node_modules/playwright/index.mjs'
import {
  classifyBrowserErrors,
  recordExpectedOptionalScheduleAbsence,
} from './browser_error_classifier.mjs'
import { adminServiceOrigin, probeAdminUnavailable } from './admin_availability.mjs'

function parseArgs(argv) {
  const result = {}
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]
    const value = argv[index + 1]
    if (!key?.startsWith('--') || !value) throw new Error(`invalid argument pair: ${key ?? ''}`)
    result[key.slice(2)] = value
  }
  for (const required of ['base-url', 'site-url', 'admin-url', 'output-dir']) {
    if (!result[required]) throw new Error(`--${required} is required`)
  }
  return result
}

const args = parseArgs(process.argv.slice(2))
const outputDir = args['output-dir']
const mode = args.mode || 'new-flow'
await fs.mkdir(outputDir, { recursive: true })
const fixture = JSON.parse(await fs.readFile('/artifacts/fixture-manifest.json', 'utf8'))
const rollback = args['base-url'].includes('api-compatible')
const adminTarget = new URL(args['admin-url'])
const adminServiceUrl = adminServiceOrigin(args['admin-url'])
const adminProxy = http.createServer((request, response) => {
  const upstream = http.request({
    hostname: adminTarget.hostname,
    port: adminTarget.port,
    path: request.url,
    method: request.method,
    headers: request.headers,
  }, (upstreamResponse) => {
    response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers)
    upstreamResponse.pipe(response)
  })
  upstream.on('error', (error) => {
    response.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' })
    response.end(`isolated Admin proxy error: ${error.message}`)
  })
  request.pipe(upstream)
})
await new Promise((resolve, reject) => {
  adminProxy.once('error', reject)
  adminProxy.listen(3901, '127.0.0.1', resolve)
})
args['admin-url'] = 'http://localhost:3901'
const browser = await chromium.launch({
  headless: true,
})
const actions = []
const errors = []
const serverErrors = []
const expectedOptionalScheduleAbsences = new Map()
const pendingBody = `task16-pending-body

## 검사 전 확인

방문 전에는 복용 중인 약과 이전 검사 경험을 의료진에게 알려 주세요. 검사 준비 방법은 개인의 건강 상태와 예약 시간에 따라 달라질 수 있으므로 병원이 안내한 순서를 먼저 확인합니다. 궁금한 점이 있으면 임의로 판단하기보다 예약한 병원에 문의해 현재 상황에 맞는 설명을 듣는 것이 좋습니다.

## 방문 당일 준비

예약 시간과 준비물, 보호자 동행 필요 여부를 다시 확인해 주세요. 평소와 다른 증상이 있거나 준비 과정에서 불편함이 생기면 그 내용을 기록해 의료진에게 전달합니다. 이 안내는 일반적인 방문 준비를 설명하며 구체적인 진료 판단은 의료진의 확인을 따릅니다.

## 진료 후 확인

진료 후에는 안내받은 주의 사항과 다음 방문 일정을 보관해 주세요. 몸 상태가 달라지거나 추가 질문이 생기면 병원 연락처로 문의해 필요한 안내를 받습니다.`
const approvedBody = pendingBody.replace('task16-pending-body', 'task16-approved-body')

function observe(page, label) {
  page.on('console', (message) => {
    if (message.type() === 'error') {
      errors.push({ label, type: 'console', text: message.text(), location: message.location() })
    }
  })
  page.on('pageerror', (error) => errors.push({ label, type: 'pageerror', text: error.message }))
  page.on('response', (response) => {
    if (response.status() >= 500) serverErrors.push({ label, status: response.status(), url: response.url() })
    recordExpectedOptionalScheduleAbsence({
      label,
      method: response.request().method(),
      status: response.status(),
      url: response.url(),
    }, expectedOptionalScheduleAbsences, {
      adminOrigin: args['admin-url'],
      hospitalId: fixture.hospitalId,
    })
  })
}

async function capture(page, name, { fullPage = true } = {}) {
  const screenshotPath = path.join(outputDir, `${name}.png`)
  await page.screenshot({ path: screenshotPath, fullPage })
  const bytes = await fs.readFile(screenshotPath)
  const layout = await page.evaluate(() => {
    const root = document.documentElement
    const korean = [...document.querySelectorAll('body *')]
      .filter((element) => element instanceof HTMLElement && /[가-힣]/.test(element.innerText || ''))
      .filter((element) => {
        const style = getComputedStyle(element)
        return style.display !== 'none' && style.visibility !== 'hidden'
      })
      .map((element) => ({
        text: (element.innerText || '').trim().slice(0, 80),
        clippedX: element.scrollWidth > element.clientWidth + 1,
        clippedY: element.scrollHeight > element.clientHeight + 1,
      }))
      .filter((sample) => sample.text)
    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      document: { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth },
      horizontalOverflow: root.scrollWidth > root.clientWidth + 1,
      koreanClippingCount: korean.filter((sample) => sample.clippedX || sample.clippedY).length,
      koreanClippingSamples: korean.filter((sample) => sample.clippedX || sample.clippedY).slice(0, 10),
    }
  })
  if (layout.horizontalOverflow) throw new Error(`${name} has page-level horizontal overflow`)
  actions.push({
    action: 'capture', name, url: page.url(),
    sha256: createHash('sha256').update(bytes).digest('hex'),
    pngSignature: bytes.subarray(0, 8).toString('hex'),
    image: { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) },
    ...layout,
    baselineComparison: {
      status: 'UNAVAILABLE_FIRST_CAPTURE',
      reason: 'No prior trustworthy screenshot exists for this changed state and viewport.',
    },
  })
}

async function authenticateAdmin(context, page) {
  await page.goto(`${args['admin-url']}/login`, { waitUntil: 'networkidle' })
  await page.getByLabel('관리자 이메일').fill('qa.owner@example.test')
  await page.getByLabel('관리자 비밀번호').fill('task16-only-password')
  const [loginResponse] = await Promise.all([
    page.waitForResponse((candidate) => candidate.url().includes('/api/auth/login')),
    page.getByRole('button', { name: '로그인' }).click(),
  ])
  if (loginResponse.status() !== 200) throw new Error(`Admin login returned ${loginResponse.status()}`)
  // The production session cookie is Secure. This isolated network is HTTP-only,
  // so Chromium correctly refuses to persist it. Keep the signed values produced
  // by the real login route, changing only the transport bit for this test origin.
  const cookiePairs = (await loginResponse.headersArray())
    .filter((header) => header.name.toLowerCase() === 'set-cookie')
    .map((header) => header.value.split(';', 1)[0])
  for (const required of ['admin_session=', 'admin_csrf=']) {
    if (!cookiePairs.some((pair) => pair.startsWith(required))) {
      throw new Error(`Admin login did not return ${required.slice(0, -1)}`)
    }
  }
  // Let the login form's router.push settle before replacing its Secure cookies.
  // Otherwise the in-flight client navigation can finish after the explicit
  // authenticated navigation below and return the page to /login.
  await page.waitForURL((url) => url.pathname === '/hospitals', { timeout: 10_000 })
    .catch(() => undefined)
  await page.waitForLoadState('networkidle').catch(() => undefined)
  // Secure-context emulation may retain the original Secure cookies. Remove
  // those before installing the single HTTP-transport copies below.
  await context.clearCookies()
  await context.addCookies(cookiePairs.map((cookiePair) => {
    const separator = cookiePair.indexOf('=')
    return {
      name: cookiePair.slice(0, separator), value: cookiePair.slice(separator + 1),
      url: args['admin-url'], httpOnly: cookiePair.startsWith('admin_session='),
      secure: false, sameSite: 'Lax',
    }
  }))
  const installedCookieNames = (await context.cookies(args['admin-url'])).map((cookie) => cookie.name).sort()
  if (!installedCookieNames.includes('admin_session') || !installedCookieNames.includes('admin_csrf')) {
    throw new Error(`signed Admin cookies were not installed: ${installedCookieNames.join(',')}`)
  }
  const hospitalsResponse = await page.goto(`${args['admin-url']}/hospitals`, { waitUntil: 'networkidle' })
  if (new URL(page.url()).pathname !== '/hospitals') {
    throw new Error(
      `signed Admin session was rejected: navigation=${hospitalsResponse?.status()} path=${new URL(page.url()).pathname}`,
    )
  }
  const secureContext = await page.evaluate(() => window.isSecureContext && Boolean(crypto.subtle))
  if (!secureContext) throw new Error('isolated Admin origin lacks Web Crypto')
  actions.push({
    action: 'login', actor: 'qa.owner@example.test', isolatedHttpCookieAdaptation: true,
    isolatedHttpSecureContextAdaptation: true,
    limitation: 'This isolated run does not prove TLS or Secure-cookie transport end to end.',
  })
  return {
    loginStatus: loginResponse.status(),
    authenticatedRouteStatus: hospitalsResponse?.status(),
    secureContext,
    installedCookieNames,
  }
}

if (mode === 'auth-preflight') {
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
    const page = await context.newPage()
    observe(page, 'admin-auth-preflight')
    const result = await authenticateAdmin(context, page)
    if (errors.length || serverErrors.length) {
      throw new Error(`browser preflight errors: ${JSON.stringify({ errors, serverErrors })}`)
    }
    await fs.writeFile(
      path.join(outputDir, 'auth-preflight.json'),
      `${JSON.stringify({ status: 'PASS', ...result, actions, errors, serverErrors }, null, 2)}\n`,
    )
    await context.close()
  } finally {
    await browser.close()
    await new Promise((resolve) => adminProxy.close(resolve))
  }
  process.exit(0)
}

try {
  const publicContext = await browser.newContext({ viewport: { width: 1280, height: 900 } })
  const publicPage = await publicContext.newPage()
  observe(publicPage, 'public')
  const articleUrl = `${args['site-url']}/${fixture.slug}/contents/${fixture.contentId}`
  const response = await publicPage.goto(articleUrl, { waitUntil: 'networkidle' })
  if (!response || response.status() !== 200) throw new Error(`public article returned ${response?.status()}`)
  const expectedPublicMarker = mode === 'new-flow' ? fixture.oldBodyMarker : 'task16-approved-body'
  await publicPage.getByText(expectedPublicMarker, { exact: false }).waitFor()
  const canonical = await publicPage.locator('link[rel="canonical"]').getAttribute('href')
  if (!canonical?.includes(fixture.slug) || !canonical.includes(fixture.contentId)) {
    throw new Error(`canonical does not identify the article: ${canonical}`)
  }
  const jsonLd = await publicPage.locator('script[type="application/ld+json"]').allTextContents()
  if (!jsonLd.some((value) => value.includes('MedicalClinic'))) {
    throw new Error('MedicalClinic JSON-LD is absent')
  }
  if ((await publicPage.locator('img[src^="javascript:"]').count()) !== 0) {
    throw new Error('unsafe image URL reached the DOM')
  }
  const publicCapturePrefix = rollback ? 'rollback' : mode === 'verify-pass' ? 'pass' : 'new'
  for (const width of [375, 768, 1280]) {
    await publicPage.setViewportSize({ width, height: 900 })
    await capture(publicPage, `${publicCapturePrefix}-public-${width}`)
  }
  await publicPage.setViewportSize({ width: 1280, height: 900 })
  const profileResponse = await publicPage.goto(`${args['site-url']}/${fixture.slug}`, { waitUntil: 'networkidle' })
  if (!profileResponse || profileResponse.status() !== 200) throw new Error(`public profile returned ${profileResponse?.status()}`)
  await publicPage.getByText('리허설 바른의원', { exact: false }).first().waitFor()
  await capture(publicPage, `${publicCapturePrefix}-profile-1280`)
  if (!rollback && mode === 'new-flow') {
    const unsafeArticleUrl = `${args['site-url']}/${fixture.slug}/contents/${fixture.unsafeContentId}`
    const unsafeResponse = await publicPage.goto(unsafeArticleUrl, { waitUntil: 'networkidle' })
    if (!unsafeResponse || unsafeResponse.status() !== 200) {
      throw new Error(`unsafe-image article returned ${unsafeResponse?.status()}`)
    }
    await publicPage.getByText('task16-unsafe-image-body', { exact: false }).waitFor()
    if ((await publicPage.locator('img[src^="javascript:"]').count()) !== 0) {
      throw new Error('unsafe image URL reached the rendered fallback article')
    }
    await capture(publicPage, 'new-public-unsafe-image-excluded-1280')
  }
  await publicContext.close()

  if (rollback) {
    const adminUnavailable = await probeAdminUnavailable(adminServiceUrl)
    if (!adminUnavailable) throw new Error('Admin remained reachable during rollback')
    actions.push({ action: 'rollback-read-only', publicStatus: 200, adminUnavailable })
  } else if (mode === 'verify-pass') {
    const adminContext = await browser.newContext({ viewport: { width: 1280, height: 900 } })
    const adminPage = await adminContext.newPage()
    observe(adminPage, 'admin-pass')
    await authenticateAdmin(adminContext, adminPage)
    await adminPage.goto(`${args['admin-url']}/hospitals/${fixture.hospitalId}/content`, { waitUntil: 'networkidle' })
    await adminPage.getByRole('button', { name: '검사 전 확인할 준비 사항 승인본' }).click()
    await adminPage.getByText('task16-approved-body', { exact: false }).waitFor()
    await capture(adminPage, 'admin-candidate-pass')
    actions.push({ action: 'candidate-pass-visible', bodyMarker: 'task16-approved-body' })
    await adminPage.getByRole('button', { name: '편집', exact: true }).click()
    const rollbackPendingBody = approvedBody.replace(
      'task16-approved-body', 'task16-rollback-pending-body',
    )
    await adminPage.getByText('본문 (마크다운)', { exact: true }).locator('..').locator('textarea').fill(rollbackPendingBody)
    const [pendingSave] = await Promise.all([
      adminPage.waitForResponse((candidate) => candidate.request().method() === 'PATCH' && candidate.url().includes('/content/')),
      adminPage.getByRole('button', { name: '저장', exact: true }).click(),
    ])
    if (pendingSave.status() !== 200) {
      throw new Error(`rollback pending candidate save returned ${pendingSave.status()}: ${(await pendingSave.text()).slice(0, 500)}`)
    }
    await adminPage.getByText('수정본 검토 중', { exact: true }).waitFor()
    actions.push({ action: 'rollback-pending-staged', bodyMarker: 'task16-rollback-pending-body' })
    await adminContext.close()
  } else {
    const adminContext = await browser.newContext({ viewport: { width: 1280, height: 900 } })
    const adminPage = await adminContext.newPage()
    observe(adminPage, 'admin')
    await authenticateAdmin(adminContext, adminPage)

    const contentUrl = `${args['admin-url']}/hospitals/${fixture.hospitalId}/content`
    await adminPage.goto(contentUrl, { waitUntil: 'networkidle' })
    await adminPage.getByRole('button', { name: fixture.oldTitle }).click()
    await adminPage.getByRole('button', { name: '편집', exact: true }).click()
    await adminPage.getByText('제목', { exact: true }).locator('..').locator('input').fill('검사 전 확인할 준비 사항 수정본')
    await adminPage.getByText('본문 (마크다운)', { exact: true }).locator('..').locator('textarea').fill(pendingBody)
    const [firstSave] = await Promise.all([
      adminPage.waitForResponse((candidate) => candidate.request().method() === 'PATCH' && candidate.url().includes('/content/')),
      adminPage.getByRole('button', { name: '저장', exact: true }).click(),
    ])
    if (firstSave.status() !== 200) {
      throw new Error(`first candidate save returned ${firstSave.status()}: ${(await firstSave.text()).slice(0, 500)}`)
    }
    await adminPage.getByText('수정본 검토 중', { exact: true }).waitFor()
    await adminPage.getByText('수정본 미리보기', { exact: true }).click()
    await adminPage.getByText('task16-pending-body', { exact: false }).waitFor()
    await capture(adminPage, 'admin-pending-preview')

    const direct = await adminContext.request.get(
      `${args['base-url']}/api/v1/public/hospitals/${fixture.slug}/contents/${fixture.contentId}`,
    )
    if (direct.status() !== 200) throw new Error(`public API during candidate returned ${direct.status()}`)
    const directBody = JSON.stringify(await direct.json())
    if (!directBody.includes(fixture.oldBodyMarker) || directBody.includes('task16-pending-body')) {
      throw new Error('pending candidate leaked through the public API')
    }
    await adminPage.getByRole('button', { name: '수정본 취소' }).click()
    await adminPage.getByText('검토 중이던 수정본을 취소했습니다.', { exact: false }).waitFor()
    await capture(adminPage, 'admin-candidate-cancelled')
    actions.push({ action: 'candidate-cancel', activeBodyPreserved: true })

    await adminPage.getByRole('button', { name: '편집', exact: true }).click()
    await adminPage.getByText('제목', { exact: true }).locator('..').locator('input').fill('검사 전 확인할 준비 사항 승인본')
    await adminPage.getByText('본문 (마크다운)', { exact: true }).locator('..').locator('textarea').fill(approvedBody)
    const [secondSave] = await Promise.all([
      adminPage.waitForResponse((candidate) => candidate.request().method() === 'PATCH' && candidate.url().includes('/content/')),
      adminPage.getByRole('button', { name: '저장', exact: true }).click(),
    ])
    if (secondSave.status() !== 200) {
      throw new Error(`second candidate save returned ${secondSave.status()}: ${(await secondSave.text()).slice(0, 500)}`)
    }
    await adminPage.getByText('수정본 검토 중', { exact: true }).waitFor()
    await capture(adminPage, 'admin-pending-pass-candidate')
    actions.push({ action: 'candidate-pass-staged', providerDecision: 'pending' })

    const reportsUrl = `${args['admin-url']}/hospitals/${fixture.hospitalId}/reports`
    await adminPage.goto(reportsUrl, { waitUntil: 'networkidle' })
    await adminPage.getByRole('heading', { name: '보고서', exact: true }).waitFor()
    const openButtons = adminPage.getByRole('button', { name: '열기', exact: true })
    if ((await openButtons.count()) !== 3) throw new Error('expected COMPLETE/LIMITED/UNAVAILABLE report rows')
    for (let index = 0; index < 3; index += 1) {
      await openButtons.nth(index).click()
      await adminPage.getByRole('dialog').waitFor()
      await adminPage.getByRole('button', { name: '원장 전달용 보고서 열기' }).waitFor()
      if (index === 0) {
        const popupPromise = adminContext.waitForEvent('page')
        await adminPage.getByRole('button', { name: '원장 전달용 보고서 열기' }).click()
        const popup = await popupPromise
        await adminPage.getByText(/내려받은 파일 확인 번호/).waitFor()
        const hashPrefix = await adminPage.getByText(/내려받은 파일 확인 번호/).textContent()
        await adminPage.getByLabel('받은 분').fill('김바른 원장')
        await adminPage.getByLabel('전달 방법').fill('대면')
        await adminPage.getByRole('button', { name: '이 파일의 원장 전달 기록 남기기' }).click()
        await adminPage.getByText('원장 전달 기록을 남겼습니다.', { exact: true }).waitFor()
        actions.push({ action: 'report-download-and-deliver', hashPrefix })
        await popup.close()
      }
      if (index === 0) {
        const internal = adminPage.locator('details[data-report-section="internal"]')
        const internalSummary = internal.locator(':scope > summary')
        await internalSummary.click()
        await internal.scrollIntoViewIfNeeded()
        await capture(adminPage, 'admin-report-dialog-1-internal', { fullPage: false })
        await internalSummary.click()
      }
      await capture(adminPage, `admin-report-dialog-${index + 1}`)
      await adminPage.getByRole('button', { name: '보고서 닫기' }).click()
    }
    await openButtons.nth(2).scrollIntoViewIfNeeded()
    await capture(adminPage, 'admin-report-list-three-statuses', { fullPage: false })
    await capture(adminPage, 'admin-report-list')
    actions.push({ action: 'reports-opened', expectedQualities: ['COMPLETE', 'LIMITED', 'UNAVAILABLE'] })

    const operationUrl = `${args['admin-url']}/operations?queue=incidents&hospital_id=${fixture.hospitalId}&detail=incident:${fixture.budgetIncidentId}`
    await adminPage.goto(operationUrl, { waitUntil: 'networkidle' })
    const detailTitle = adminPage.locator('#ops-detail-title')
    await detailTitle.waitFor()
    const detailTitleText = (await detailTitle.textContent())?.trim()
    if (detailTitleText !== '리허설 바른의원 · 처리 필요') {
      throw new Error(`unexpected operations detail title: ${detailTitleText ?? '<missing>'}`)
    }
    const resetButton = adminPage.getByRole('button', { name: '레거시 예산 교체 후 다시 시도' })
    await resetButton.waitFor()
    await capture(adminPage, 'admin-budget-reset-available')
    await adminPage.getByLabel('처리 사유').fill('Task 16 격리 리허설에서 이전 사용량을 확인할 수 없어 교체합니다.')
    await resetButton.scrollIntoViewIfNeeded()
    await capture(adminPage, 'admin-budget-reset-action-ready', { fullPage: false })
    const [resetResponse] = await Promise.all([
      adminPage.waitForResponse((candidate) => candidate.request().method() === 'POST' && candidate.url().includes(`/content/${fixture.budgetContentId}/regenerate`)),
      resetButton.click(),
    ])
    const resetBody = await resetResponse.text()
    if (resetResponse.status() !== 200) throw new Error(`legacy budget reset returned ${resetResponse.status()}: ${resetBody.slice(0, 500)}`)
    const resetResponsePayload = JSON.parse(resetBody)
    const resetRequest = resetResponse.request()
    const resetRequestBody = JSON.parse(resetRequest.postData() || '{}')
    const firstIdempotencyKey = await resetRequest.headerValue('idempotency-key')
    if (!firstIdempotencyKey || resetRequestBody.reason !== 'Task 16 격리 리허설에서 이전 사용량을 확인할 수 없어 교체합니다.') {
      const reason = typeof resetRequestBody.reason === 'string' ? resetRequestBody.reason : ''
      throw new Error(`legacy budget reset request contract mismatch: ${JSON.stringify({
        idempotencyKeyPresent: Boolean(firstIdempotencyKey),
        bodyKeys: Object.keys(resetRequestBody).sort(),
        reasonLength: reason.length,
        reasonSha256: createHash('sha256').update(reason).digest('hex'),
      })}`)
    }
    await adminPage.getByText('서버 확인 중…', { exact: true }).waitFor({ state: 'detached' })
    const immediateIncident = await adminPage.evaluate(async ({ hospitalId, incidentId }) => {
      const response = await fetch(`/api/admin/operations/hospitals/${hospitalId}/incidents/${incidentId}`)
      return { status: response.status, body: await response.json() }
    }, { hospitalId: fixture.hospitalId, incidentId: fixture.budgetIncidentId })
    const immediateRetries = [
      immediateIncident.body?.incident?.retry ?? null,
      immediateIncident.body?.run?.retry ?? null,
    ]
    if (immediateIncident.status !== 200 || immediateRetries.some(
      (retry) => retry?.kind === 'POST_ACTION' && retry?.enabled === true,
    )) {
      throw new Error(`spent legacy reset remained an enabled immediate capability: ${JSON.stringify({
        status: immediateIncident.status,
        incidentRunId: immediateIncident.body?.incident?.operation_run_id ?? null,
        incidentRetry: immediateRetries[0],
        runId: immediateIncident.body?.run?.run_id ?? null,
        runState: immediateIncident.body?.run?.state ?? null,
        runRetry: immediateRetries[1],
      })}`)
    }
    const secondIdempotencyKey = crypto.randomUUID()
    const duplicate = await adminPage.evaluate(async ({ hospitalId, contentId, idempotencyKey }) => {
      const csrf = document.cookie.split('; ').find((pair) => pair.startsWith('admin_csrf='))?.split('=', 2)[1]
      const response = await fetch(`/api/admin/hospitals/${hospitalId}/content/${contentId}/regenerate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
          'X-Admin-CSRF-Token': csrf || '',
        },
        body: JSON.stringify({ reason: 'Task 16 두 번째 교체 차단 확인' }),
      })
      return { status: response.status, body: await response.text(), idempotencyKey, method: 'POST', url: response.url }
    }, { hospitalId: fixture.hospitalId, contentId: fixture.budgetContentId, idempotencyKey: secondIdempotencyKey })
    if (duplicate.status !== 409 || !duplicate.body.includes('already replaced')) {
      throw new Error(`second legacy budget reset was not blocked: ${JSON.stringify(duplicate)}`)
    }
    if (duplicate.method !== 'POST' || !duplicate.url.includes(`/content/${fixture.budgetContentId}/regenerate`)) {
      throw new Error(`second legacy budget reset targeted an unexpected request: ${JSON.stringify(duplicate)}`)
    }
    const expectedConflictEntries = errors.filter((entry) => entry.label === 'admin'
      && entry.type === 'console'
      && entry.text === 'Failed to load resource: the server responded with a status of 409 (Conflict)')
    if (expectedConflictEntries.length > 1) {
      throw new Error(`ambiguous 409 console errors around expected conflict: ${JSON.stringify(expectedConflictEntries)}`)
    }
    const expectedConflict = errors.indexOf(expectedConflictEntries[0])
    if (expectedConflict >= 0) errors.splice(expectedConflict, 1)
    await capture(adminPage, 'admin-budget-reset-result')
    actions.push({
      action: 'legacy-budget-reset-real-click', status: resetResponse.status(),
      response: resetResponsePayload, contentId: fixture.budgetContentId,
      request: { reason: resetRequestBody.reason, idempotencyKey: firstIdempotencyKey },
      secondAttempt: duplicate, expectedConflictConsoleConsumed: expectedConflict >= 0,
      immediateProjection: {
        incidentRunId: immediateIncident.body.incident.operation_run_id,
        incidentRetry: immediateRetries[0],
        runId: immediateIncident.body.run?.run_id ?? null,
        runState: immediateIncident.body.run?.state ?? null,
        runRetry: immediateRetries[1],
      },
    })
    const replacementRunId = resetResponsePayload.operation_run_id
    if (typeof replacementRunId !== 'string' || !replacementRunId) {
      throw new Error('legacy budget reset response omitted the replacement operation run id')
    }
    const replacementDeadline = Date.now() + 90_000
    const replacementStates = []
    let replacementProjection = null
    while (Date.now() < replacementDeadline) {
      replacementProjection = await adminPage.evaluate(async ({ hospitalId, runId }) => {
        const response = await fetch(`/api/admin/operations/hospitals/${hospitalId}/runs/${runId}`)
        return { status: response.status, body: await response.json() }
      }, { hospitalId: fixture.hospitalId, runId: replacementRunId })
      if (replacementProjection.status !== 200) {
        throw new Error(`replacement run projection returned ${replacementProjection.status}`)
      }
      replacementStates.push(replacementProjection.body.state)
      if (['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(replacementProjection.body.state)) break
      await adminPage.waitForTimeout(500)
    }
    if (replacementProjection?.body?.state !== 'SUCCEEDED' || replacementProjection.body.retry !== null) {
      throw new Error(`replacement run did not settle without retry: ${JSON.stringify({
        runId: replacementRunId,
        states: replacementStates,
        terminalState: replacementProjection?.body?.state ?? null,
        retry: replacementProjection?.body?.retry ?? null,
      })}`)
    }
    const settledIncident = await adminPage.evaluate(async ({ hospitalId, incidentId }) => {
      const response = await fetch(`/api/admin/operations/hospitals/${hospitalId}/incidents/${incidentId}`)
      return { status: response.status, body: await response.json() }
    }, { hospitalId: fixture.hospitalId, incidentId: fixture.budgetIncidentId })
    if (settledIncident.status !== 200
      || settledIncident.body?.incident?.operation_run_id !== replacementRunId
      || settledIncident.body?.incident?.retry !== null
      || settledIncident.body?.run?.run_id !== replacementRunId
      || settledIncident.body?.run?.state !== 'SUCCEEDED'
      || settledIncident.body?.run?.retry !== null) {
      throw new Error(`canonical incident did not settle on replacement run: ${JSON.stringify({
        status: settledIncident.status,
        incidentRunId: settledIncident.body?.incident?.operation_run_id ?? null,
        incidentRetry: settledIncident.body?.incident?.retry ?? null,
        runId: settledIncident.body?.run?.run_id ?? null,
        runState: settledIncident.body?.run?.state ?? null,
        runRetry: settledIncident.body?.run?.retry ?? null,
      })}`)
    }
    await adminPage.reload({ waitUntil: 'networkidle' })
    await adminPage.locator('#ops-detail-title').waitFor()
    const resetButtonsAfterReload = await adminPage.getByRole('button', { name: '레거시 예산 교체 후 다시 시도' }).count()
    if (resetButtonsAfterReload !== 0) {
      throw new Error(`legacy budget reset action remained available after confirmed replacement: ${resetButtonsAfterReload}`)
    }
    const nextActionHeading = adminPage.getByRole('heading', { name: '지금 할 일', exact: true })
    await nextActionHeading.scrollIntoViewIfNeeded()
    await capture(adminPage, 'admin-budget-reset-confirmed-state', { fullPage: false })
    actions.push({
      action: 'legacy-budget-reset-visible-confirmed-state',
      replacementRunId,
      replacementStates,
      terminalState: replacementProjection.body.state,
      canonicalIncidentRunId: settledIncident.body.incident.operation_run_id,
      resetActionAvailable: false,
    })
    await adminContext.close()
  }
} finally {
  await browser.close()
  await new Promise((resolve) => adminProxy.close(resolve))
}

const actionPayload = `${JSON.stringify({ mode, rollback, actions, errors, serverErrors }, null, 2)}\n`
await fs.writeFile(path.join(outputDir, `actions-${mode}.json`), actionPayload)
await fs.writeFile(path.join(outputDir, 'actions.json'), actionPayload)
const visualManifestPath = path.join(outputDir, 'visual-capture-manifest.json')
let visualManifest = {
  schemaVersion: 1,
  reviewTracks: [
    'functional-and-design-token-source-review',
    'visual-layout-and-korean-clipping-review',
  ],
  sessions: [],
}
try {
  visualManifest = JSON.parse(await fs.readFile(visualManifestPath, 'utf8'))
} catch (error) {
  if (error?.code !== 'ENOENT') throw error
}
const captureContext = {
  'new-public-375': 'Published article, no-image fallback, 375px viewport',
  'new-public-768': 'Published article, no-image fallback, 768px viewport',
  'new-public-1280': 'Published article, no-image fallback, 1280px viewport',
  'new-profile-1280': 'Public hospital profile, 1280px viewport',
  'new-public-unsafe-image-excluded-1280': 'Approved text remains visible while unsafe image URL is excluded',
  'admin-pending-preview': 'Admin candidate pending preview',
  'admin-candidate-cancelled': 'Admin candidate cancellation result',
  'admin-pending-pass-candidate': 'Admin candidate awaiting automated PASS',
  'admin-report-dialog-1': 'COMPLETE report delivery dialog after bound download and delivery event',
  'admin-report-dialog-1-internal': 'COMPLETE report internal quality evidence expanded and scrolled into view',
  'admin-report-dialog-2': 'LIMITED report delivery dialog',
  'admin-report-dialog-3': 'UNAVAILABLE report delivery dialog',
  'admin-report-list': 'Three report quality states in the report list',
  'admin-report-list-three-statuses': 'Three report quality rows scrolled into the active viewport',
  'admin-budget-reset-available': 'Operation detail with one-time legacy budget reset available',
  'admin-budget-reset-action-ready': 'One-time legacy budget reset reason and action visible in the active viewport',
  'admin-budget-reset-result': 'Operation detail after reset success and second-attempt rejection',
  'admin-budget-reset-confirmed-state': 'Reloaded operation detail after reset with the one-time action no longer available',
  'admin-candidate-pass': 'Admin approved candidate after real PASS promotion',
  'pass-public-375': 'PASS-promoted published article, 375px viewport',
  'pass-public-768': 'PASS-promoted published article, 768px viewport',
  'pass-public-1280': 'PASS-promoted published article, 1280px viewport',
  'pass-profile-1280': 'Public hospital profile after PASS promotion, 1280px viewport',
  'rollback-public-375': 'Compatible reader article with pending candidate hidden, 375px viewport',
  'rollback-public-768': 'Compatible reader article with pending candidate hidden, 768px viewport',
  'rollback-public-1280': 'Compatible reader article with pending candidate hidden, 1280px viewport',
  'rollback-profile-1280': 'Compatible reader public profile, 1280px viewport',
}
const captures = actions
  .filter((entry) => entry.action === 'capture')
  .map((entry) => ({ ...entry, context: captureContext[entry.name] || entry.name }))
visualManifest.sessions = visualManifest.sessions.filter((entry) => entry.mode !== mode)
visualManifest.sessions.push({ mode, rollback, captures })
visualManifest.sessions.sort((left, right) => left.mode.localeCompare(right.mode))
const observedModes = new Set(visualManifest.sessions.map((entry) => entry.mode))
visualManifest.completeForReview = ['new-flow', 'verify-pass', 'rollback']
  .every((expected) => observedModes.has(expected))
visualManifest.limitations = [
  'No trustworthy pre-refactor screenshot exists for pixel-diff comparison; every capture records this explicitly.',
  'The isolated HTTP cookie adaptation does not prove TLS or Secure-cookie transport end to end.',
]
await fs.writeFile(visualManifestPath, `${JSON.stringify(visualManifest, null, 2)}\n`)
const {
  classified: classifiedScheduleAbsences,
  unexpected: unexpectedErrors,
} = classifyBrowserErrors(errors, expectedOptionalScheduleAbsences)
if (classifiedScheduleAbsences.length) {
  actions.push({ action: 'expected-optional-schedule-absence', requests: classifiedScheduleAbsences })
  await fs.writeFile(path.join(outputDir, `actions-${mode}.json`), `${JSON.stringify({ mode, rollback, actions, errors: unexpectedErrors, serverErrors }, null, 2)}\n`)
  await fs.writeFile(path.join(outputDir, 'actions.json'), `${JSON.stringify({ mode, rollback, actions, errors: unexpectedErrors, serverErrors }, null, 2)}\n`)
}
if (unexpectedErrors.length || serverErrors.length) {
  throw new Error(`browser errors: ${JSON.stringify({ errors: unexpectedErrors, serverErrors })}`)
}
