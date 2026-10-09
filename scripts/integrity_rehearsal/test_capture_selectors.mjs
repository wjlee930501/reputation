import { chromium } from 'playwright'

const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  await page.setContent(`
    <main>
      <button>열기</button><button>열기</button><button>열기</button>
      <div role="dialog">
        <details data-report-section="internal">
          <summary>내부 상태 · 원장 전달 금지</summary>
          <details><summary>질문별 측정 근거 0건</summary></details>
          <details><summary>발행 상태와 검수 기준 확인</summary></details>
        </details>
      </div>
      <aside>
        <h3>지금 할 일</h3>
        <label>처리 사유<textarea></textarea></label>
        <button id="reset">레거시 예산 교체 후 다시 시도</button>
      </aside>
    </main>
  `)

  const internalSummary = page
    .locator('details[data-report-section="internal"]')
    .locator(':scope > summary')
  if ((await internalSummary.count()) !== 1 || !(await internalSummary.isVisible())) {
    throw new Error('internal direct-summary selector is not unique and visible')
  }
  const openButtons = page.getByRole('button', { name: '열기', exact: true })
  if ((await openButtons.count()) !== 3 || !(await openButtons.nth(2).isVisible())) {
    throw new Error('third report-row selector is not unique and visible')
  }
  const reason = page.getByLabel('처리 사유')
  const reset = page.getByRole('button', { name: '레거시 예산 교체 후 다시 시도' })
  if ((await reason.count()) !== 1 || !(await reason.isVisible())
    || (await reset.count()) !== 1 || !(await reset.isVisible())) {
    throw new Error('budget reason/action selectors are not unique and visible')
  }
  const nextAction = page.getByRole('heading', { name: '지금 할 일', exact: true })
  if ((await nextAction.count()) !== 1 || !(await nextAction.isVisible())) {
    throw new Error('post-action state heading selector is not unique and visible')
  }
  await page.locator('#reset').evaluate((element) => element.remove())
  if ((await reset.count()) !== 0) {
    throw new Error('post-action reset absence selector did not observe removal')
  }
  process.stdout.write(`${JSON.stringify({
    status: 'passed',
    selectors: [
      'details[data-report-section=internal] > summary',
      'third exact report open button',
      'budget reason and exact action button',
      'exact next-action heading and reset absence',
    ],
  })}\n`)
} finally {
  await browser.close()
}
