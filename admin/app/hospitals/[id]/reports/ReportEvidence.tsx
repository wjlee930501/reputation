import Link from 'next/link'
import type { ActionCopy, ReportView } from '@/lib/report-review'

function dateTime(value: string | null): string {
  if (!value) return '확인 기록 없음'
  return new Date(value).toLocaleString('ko-KR')
}

export function ReportEvidence({ report, onCopyNotification }: { report: ReportView; onCopyNotification: () => void }) {
  const review = report.review
  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-[var(--color-revisit-primary-80)] bg-[var(--color-revisit-primary-95)] p-4" aria-labelledby="review-status-heading" data-review-section="status">
        <p className="text-xs font-bold text-[var(--color-revisit-nav)]">{review?.versionLabel ?? '버전 정보를 확인할 수 없습니다'}</p>
        <h4 id="review-status-heading" className="mt-1 text-lg font-bold text-[var(--color-revisit-text-title)] [word-break:keep-all]">
          {report.deliveryReady ? '원장 전달 전 근거를 최종 확인해 주세요' : '현재 리포트는 원장님께 전달할 수 없습니다'}
        </h4>
        {!report.deliveryReady && (
          <dl className="mt-3 grid gap-3 text-sm leading-6 md:grid-cols-3">
            <Copy label="무슨 문제인지" value={report.deliveryBlockers[0] ?? '최신 전달 가능 상태를 확인할 수 없습니다.'} />
            <Copy label="고객 영향" value="확인되지 않은 자료를 전달하면 원장님께 잘못된 결과를 설명할 수 있습니다." />
            <Copy label="지금 할 일" value="아래 측정·파일·운영 기준 근거를 확인하고 안내된 조치를 실행해 주세요." />
          </dl>
        )}
      </section>

      <section className="rounded-xl border border-[var(--color-revisit-coolgrey-20)] p-4" aria-labelledby="measurement-heading" data-review-section="measurement">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><p className="text-xs font-bold text-[var(--color-revisit-text-caption)]">측정 근거</p><h4 id="measurement-heading" className="mt-1 font-bold text-[var(--color-revisit-text-title)]">{review?.measurement.label ?? '측정 상태 확인 필요'}</h4></div>
          <p className="text-sm font-semibold text-[var(--color-revisit-text-helper)]">AI 답변 내 병원 언급률 {report.sovPct === null ? '확인 불가' : `${report.sovPct.toFixed(1)}%`}</p>
        </div>
        {review && <EvidenceCopy copy={review.measurement} />}
        {review && (
          <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Count label="계획" value={review.measurement.plannedCount} />
            <Count label="측정 완료" value={review.measurement.successCount} />
            <Count label="실패" value={review.measurement.failedCount} />
            <Count label="측정 제외" value={review.measurement.excludedCount} />
          </dl>
        )}
        <details className="mt-4 rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3" open>
          <summary className="flex min-h-11 cursor-pointer items-center font-bold text-[var(--color-revisit-text-title)]">질문별 측정 근거 {report.cells.length}건</summary>
          {report.cells.length ? (
            <ul className="mt-3 grid gap-2">
              {report.cells.map((cell) => (
                <li key={`${cell.queryKey}-${cell.platformLabel}`} className="rounded-lg bg-white p-3 text-sm leading-5">
                  <div className="flex flex-wrap justify-between gap-2"><strong>{cell.queryLabel} · {cell.platformLabel}</strong><span>{cell.stateLabel}</span></div>
                  <p className="mt-1 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">{cell.queryText}</p>
                  <p className="mt-1 text-xs font-semibold">병원 언급: {cell.measured ? (cell.mentioned ? '확인됨' : '확인되지 않음') : '측정되지 않음'}</p>
                </li>
              ))}
            </ul>
          ) : <div className="mt-2 text-sm text-[var(--color-revisit-red-50)]"><p>문제: 질문별 측정 근거가 없습니다.<br />고객 영향: 이 화면만으로 수치가 충분히 측정됐는지 확인할 수 없습니다.<br />지금 할 일: 운영 센터에서 측정 기록을 확인해 주세요.</p><EvidenceActions operationsUrl={review?.notification.operationsUrl} onCopy={onCopyNotification} /></div>}
        </details>
      </section>

      {report.comparison && (
        <section className="rounded-xl border border-[var(--color-revisit-coolgrey-20)] p-4" aria-labelledby="comparison-heading">
          <h4 id="comparison-heading" className="font-bold text-[var(--color-revisit-text-title)]">지난달과 비교</h4>
          <p className="mt-2 text-sm font-semibold">{report.comparison.comparable ? '같은 질문과 AI 서비스 기준으로 비교할 수 있습니다.' : '지난달과 직접 비교할 수 없습니다.'}</p>
          <EvidenceCopy copy={{ label: '', ...report.comparison }} />
        </section>
      )}

      {report.mentions.length > 0 && (
        <section className="rounded-xl border border-[var(--color-revisit-coolgrey-20)] p-4" aria-labelledby="mention-heading">
          <h4 id="mention-heading" className="font-bold text-[var(--color-revisit-text-title)]">언급 변화 근거</h4>
          <div className="mt-3 grid gap-3">
            {report.mentions.map((item, index) => (
              <article key={`${item.queryText}-${item.platformLabel}-${index}`} className="rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3 text-sm">
                <h5 className="font-bold">{item.label} · {item.platformLabel}</h5><p className="mt-1">{item.queryText}</p>
                <EvidenceCopy copy={item} />
                {item.relatedContents.length > 0 && <p className="mt-2 text-xs text-[var(--color-revisit-text-helper)]">같은 기간 관련 콘텐츠: {item.relatedContents.join(', ')}</p>}
              </article>
            ))}
          </div>
        </section>
      )}

      <section className="rounded-xl border border-[var(--color-revisit-coolgrey-20)] p-4" aria-labelledby="artifact-heading" data-review-section="artifact">
        <h4 id="artifact-heading" className="font-bold text-[var(--color-revisit-text-title)]">원장 전달용 파일 검증</h4>
        <p className="mt-2 text-sm font-semibold">{report.doctorArtifact.stateLabel}</p>
        <p className="mt-1 text-sm text-[var(--color-revisit-text-helper)]">{report.doctorArtifact.pageCount ? `${report.doctorArtifact.pageCount}페이지` : '페이지 수 확인 불가'} · 검증 {dateTime(report.doctorArtifact.validatedAt)}</p>
        {report.doctorArtifact.state !== 'VALID' && <div className="mt-2 text-sm text-[var(--color-revisit-red-50)]"><p>문제: 원장 전달용 파일 검증이 끝나지 않았습니다.<br />고객 영향: 검증되지 않은 파일은 원장님께 전달할 수 없습니다.<br />지금 할 일: 운영 센터에서 차단 사유를 확인하고 리포트를 다시 만들어 주세요.</p><EvidenceActions operationsUrl={review?.notification.operationsUrl} onCopy={onCopyNotification} /></div>}
      </section>

      {review && (
        <section className="rounded-xl border border-[var(--color-revisit-coolgrey-20)] p-4" aria-labelledby="notification-heading" data-review-section="notification">
          <h4 id="notification-heading" className="font-bold text-[var(--color-revisit-text-title)]">운영팀 Slack 알림 · 리포트 성공과 별도</h4>
          <p className="mt-2 text-sm font-semibold">{review.notification.label}</p>
          <EvidenceCopy copy={review.notification} />
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <Link href={review.notification.operationsUrl} className="inline-flex min-h-11 items-center justify-center rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white">운영 센터에서 최신 Slack 알림 확인</Link>
            <button type="button" onClick={onCopyNotification} className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold">개발팀 문의용 정보 복사</button>
          </div>
        </section>
      )}
    </div>
  )
}

function EvidenceCopy({ copy }: { copy: ActionCopy }) {
  return <dl className="mt-3 grid gap-3 text-sm leading-6 md:grid-cols-3"><Copy label="무슨 문제인지" value={copy.problem} /><Copy label="고객 영향" value={copy.customerImpact} /><Copy label="지금 할 일" value={copy.nextAction} /></dl>
}
function EvidenceActions({ operationsUrl, onCopy }: { operationsUrl?: string; onCopy: () => void }) { return <div className="mt-3 flex flex-col gap-2 sm:flex-row"><Link href={operationsUrl ?? '/operations?queue=REPORTS'} className="inline-flex min-h-11 items-center justify-center rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white">운영 센터에서 확인</Link><button type="button" onClick={onCopy} className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold text-[var(--color-revisit-text-title)]">개발팀 문의용 정보 복사</button></div> }
function Copy({ label, value }: { label: string; value: string }) { return <div><dt className="font-bold">{label}</dt><dd className="mt-1 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">{value}</dd></div> }
function Count({ label, value }: { label: string; value: number }) { return <div className="rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3"><dt className="text-xs text-[var(--color-revisit-text-helper)]">{label}</dt><dd className="mt-1 text-xl font-bold">{value}</dd></div> }
