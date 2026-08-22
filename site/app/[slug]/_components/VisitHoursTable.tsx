import { buildWeeklyHoursRows, VISIT_HOURS_ANCHOR } from '@/lib/business-hours'

import { CalendarIcon } from './icons'

interface Props {
  hospitalName: string
  phone: string
  businessHours: Record<string, string> | null | undefined
}

/**
 * `/visit`의 요일별 진료시간 표.
 *
 * JSON-LD와 같은 business_hours를 읽으므로 답변 엔진이 보는 값과 환자가 보는 값이
 * 같다. 값이 없는 요일은 비워 두고 표시하며, 병원이 아직 진료시간을 주지 않았으면
 * 표 대신 전화 확인 안내를 낸다 — 없는 시간을 채워 넣지 않는다.
 */
export function VisitHoursTable({ hospitalName, phone, businessHours }: Props) {
  const rows = buildWeeklyHoursRows(businessHours)
  const hasHours = rows.some((row) => row.value !== null)
  const closedDays = rows.filter((row) => row.closed).map((row) => row.label)

  return (
    <section id={VISIT_HOURS_ANCHOR} className="clinic-section clinic-section--hours">
      <div className="clinic-section-inner">
        <header className="clinic-section-head">
          <h2 className="clinic-section-title">
            <CalendarIcon className="clinic-icon clinic-icon--sm" aria-hidden="true" />
            {hospitalName} 요일별 진료시간
          </h2>
          <p className="clinic-section-note">
            공휴일과 병원 사정에 따라 달라질 수 있습니다. 방문 전 전화로 확인해 주세요.
          </p>
        </header>

        {hasHours ? (
          <table className="clinic-hours-table">
            <caption className="clinic-hours-caption">
              {hospitalName} 요일별 진료시간 안내
            </caption>
            <thead>
              <tr>
                <th scope="col">요일</th>
                <th scope="col">진료시간</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key} className={row.closed ? 'is-closed' : undefined}>
                  <th scope="row">{row.label}</th>
                  <td>{row.value ?? '진료시간 확인 중'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="clinic-hours-empty">
            공개된 요일별 진료시간이 아직 없습니다.{' '}
            <a href={`tel:${phone}`}>{phone}</a>으로 문의해 주세요.
          </p>
        )}

        {closedDays.length > 0 && (
          <p className="clinic-week-notice">
            <span aria-hidden="true" className="clinic-week-notice-dot" />
            휴진 안내 — {closedDays.join(', ')}에는 진료하지 않습니다.
          </p>
        )}
      </div>
    </section>
  )
}
