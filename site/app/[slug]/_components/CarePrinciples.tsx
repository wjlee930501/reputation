import Link from 'next/link'

interface Props {
  hospitalSlug: string
  directorName: string
  specialties: string[]
}

/* 진료 철학 아이콘 — 인라인 SVG, 장식용이므로 aria-hidden */
function IconDiagnose() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.35-4.35" />
      <path d="M11 8v6M8 11h6" />
    </svg>
  )
}
function IconNoSurgery() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2a10 10 0 1 0 0 20A10 10 0 0 0 12 2z" />
      <path d="M9 12h6" />
    </svg>
  )
}
function IconCare() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2 4 6v6c0 5 3 9 8 10 5-1 8-5 8-10V6z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  )
}

export function CarePrinciples({ hospitalSlug, directorName, specialties }: Props) {
  const specialtyText = specialties.length > 0 ? specialties.join(', ') : '진료 영역'

  return (
    <section className="clinic-section clinic-section--principles">
      <div className="clinic-section-inner">
        <header className="clinic-section-header clinic-section-header--center">
          <span className="clinic-section-label">진료 철학</span>
          <h2 className="clinic-section-heading">환자 상태를 먼저 확인합니다</h2>
          <p className="clinic-section-lede">
            {directorName} 원장은 {specialtyText} 진료에서 치료보다 정확한 원인 파악을 먼저 합니다.
          </p>
        </header>

        <div className="clinic-belief-grid">
          <BeliefCard
            Icon={IconDiagnose}
            title="원인부터 파악합니다"
            body="같은 부위 통증이라도 원인이 다릅니다. 진찰·문진·영상검사를 종합해 실제 문제를 확인합니다."
          />
          <BeliefCard
            Icon={IconNoSurgery}
            title="수술은 마지막 선택입니다"
            body="비수술 치료로 충분히 호전되는 경우가 많습니다. 약물·주사·물리치료·도수재활 순으로 상담합니다."
          />
          <BeliefCard
            Icon={IconCare}
            title="재발 없는 회복을 목표합니다"
            body="통증 감소뿐 아니라 재발을 줄이는 운동 복귀 기준과 생활 관리 방법을 함께 안내합니다."
          />
        </div>

        <div className="clinic-principles-actions">
          <Link href={`/${hospitalSlug}/doctor`}>의료진 보기</Link>
          <Link href={`/${hospitalSlug}/contents`}>전체 글 보기</Link>
          <Link href={`/${hospitalSlug}#contact`}>공식 채널 보기</Link>
        </div>
      </div>
    </section>
  )
}

function BeliefCard({
  Icon,
  title,
  body,
}: {
  Icon: () => JSX.Element
  title: string
  body: string
}) {
  return (
    <article className="clinic-belief-card">
      <span className="clinic-belief-icon" aria-hidden="true">
        <Icon />
      </span>
      <h3 className="clinic-belief-title">{title}</h3>
      <p className="clinic-belief-body">{body}</p>
    </article>
  )
}
