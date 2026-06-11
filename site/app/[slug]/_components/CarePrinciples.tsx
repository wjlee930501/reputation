import Link from 'next/link'

interface Props {
  hospitalSlug: string
  specialties: string[]
}

/* 진료 원칙 아이콘 — 인라인 SVG, 장식용이므로 aria-hidden */
function IconExplain() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      <path d="M8 9h8M8 13h5" />
    </svg>
  )
}
function IconAccountable() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.35-4.35" />
      <path d="M11 8v6M8 11h6" />
    </svg>
  )
}
function IconTogether() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2 4 6v6c0 5 3 9 8 10 5-1 8-5 8-10V6z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  )
}

/* 임상적 주장(치료 순서·수술 여부 등)을 만들어 원장 발언처럼 노출하지 않는다.
   여기서는 안내·설명·상담 방식에 대한 비임상 운영 원칙만 다룬다. */
export function CarePrinciples({ hospitalSlug, specialties }: Props) {
  const specialtyText = specialties.filter(Boolean).join(', ')

  return (
    <section className="clinic-section clinic-section--principles">
      <div className="clinic-section-inner">
        <header className="clinic-section-header clinic-section-header--center">
          <span className="clinic-section-label">진료 안내 원칙</span>
          <h2 className="clinic-section-heading">설명과 상담을 우선합니다</h2>
          <p className="clinic-section-lede">
            {specialtyText
              ? `${specialtyText} 진료 안내에서 지키는 기본 원칙입니다.`
              : '진료 안내에서 지키는 기본 원칙입니다.'}
          </p>
        </header>

        <div className="clinic-belief-grid">
          <BeliefCard
            Icon={IconExplain}
            title="충분히 설명합니다"
            body="검사와 진료 과정을 환자가 이해할 수 있는 언어로 설명하고, 궁금한 점을 확인한 뒤 진행합니다."
          />
          <BeliefCard
            Icon={IconAccountable}
            title="확인된 정보만 안내합니다"
            body="이 페이지의 진료 안내와 의료 정보는 출처와 업데이트 일자를 함께 표기하며, 과장된 표현을 사용하지 않습니다."
          />
          <BeliefCard
            Icon={IconTogether}
            title="진료 상담으로 함께 결정합니다"
            body="치료 방향은 글이 아니라 진료실에서의 상담과 개인별 상태 확인을 거쳐 결정됩니다."
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
