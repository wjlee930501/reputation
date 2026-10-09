import fs from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { chromium } from '/runner/node_modules/playwright/index.mjs'

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, index, values) => {
    if (index % 2 === 0) pairs.push([value.replace(/^--/, ''), values[index + 1]])
    return pairs
  }, []),
)
for (const required of ['api-url', 'site-url', 'output-dir']) {
  if (!args[required]) throw new Error(`--${required} is required`)
}

const fixture = JSON.parse(await fs.readFile('/artifacts/fixture-manifest.json', 'utf8'))
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const apiResponse = await context.request.get(
  `${args['api-url']}/api/v1/public/hospitals/${fixture.slug}/contents/${fixture.contentId}`,
)
if (apiResponse.status() !== 200) throw new Error(`compatible API returned ${apiResponse.status()}`)
const apiBody = await apiResponse.text()
if (!apiBody.includes(fixture.oldBodyMarker) || apiBody.includes('task16-pending-body')) {
  throw new Error('compatible API did not expose only the active body')
}

const page = await context.newPage()
const errors = []
page.on('console', (message) => {
  if (message.type() === 'error') errors.push({ type: 'console', text: message.text(), location: message.location() })
})
page.on('pageerror', (error) => errors.push({ type: 'pageerror', text: error.message }))
page.on('response', (response) => {
  if (response.status() >= 500) errors.push({ type: 'server', status: response.status(), url: response.url() })
})
const articleUrl = `${args['site-url']}/${fixture.slug}/contents/${fixture.contentId}`
const siteResponse = await page.goto(articleUrl, { waitUntil: 'networkidle' })
if (siteResponse?.status() !== 200) throw new Error(`rollback Site article returned ${siteResponse?.status()}`)
const body = await page.locator('body').innerText()
if (!body.includes(fixture.oldBodyMarker) || body.includes('task16-pending-body')) {
  throw new Error('rollback Site did not expose only the active body')
}
if (errors.length) throw new Error(`rollback Site browser errors: ${JSON.stringify(errors)}`)

await fs.mkdir(args['output-dir'], { recursive: true })
const screenshotPath = path.join(args['output-dir'], 'rollback-site-smoke.png')
await page.screenshot({ path: screenshotPath, fullPage: true })
const screenshot = await fs.readFile(screenshotPath)
const result = {
  status: 'PASS',
  apiStatus: apiResponse.status(),
  siteStatus: siteResponse.status(),
  activeBodyVisible: true,
  pendingCandidateHidden: true,
  errors,
  screenshot: {
    path: screenshotPath,
    sha256: createHash('sha256').update(screenshot).digest('hex'),
    pngSignature: screenshot.subarray(0, 8).toString('hex'),
    width: screenshot.readUInt32BE(16),
    height: screenshot.readUInt32BE(20),
  },
}
await fs.writeFile(path.join(args['output-dir'], 'result.json'), `${JSON.stringify(result, null, 2)}\n`)
console.log(JSON.stringify(result))
await browser.close()
