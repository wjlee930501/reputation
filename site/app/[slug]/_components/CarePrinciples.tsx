import Link from 'next/link'

interface Props {
  hospitalSlug: string
  specialties: string[]
}

/* 임상적 주장(치료 순서·수술 여부 등)을 만들어 원장 발언처럼 노출하지 않는다.
   여기서는 안내·설명·상담 방식에 대한 비임상 운영 원칙만 다룬다. */
export function CarePrinciples({ hospitalSlug, specialties }: Props) {
  const specialtyText = specialties.filter(Boolean).join(', ')

  return (
    <section className="clinic-section clinic-section--principles">
      <div className="clinic-section-inner">
        <header className="clinic-section-head clinic-section-head--center">
          <h2 className="clinic-section-title">설명과 상담을 우선합니다</h2>
          <p className="clinic-section-note">
            {specialtyText
              ? `${specialtyText} 진료 안내에서 지키는 기본 원칙입니다.`
              : '진료 안내에서 지키는 기본 원칙입니다.'}
          </p>
        </header>

        <div className="clinic-belief-grid">
          <BeliefCard
            title="충분히 설명합니다"
            body="검사와 진료 과정을 환자가 이해할 수 있는 언어로 설명하고, 궁금한 점을 확인한 뒤 진행합니다."
          />
          <BeliefCard
            title="확인된 정보만 안내합니다"
            body="이 페이지의 진료 안내와 의료 정보는 출처와 업데이트 일자를 함께 표기하며, 과장된 표현을 사용하지 않습니다."
          />
          <BeliefCard
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
  title,
  body,
}: {
  title: string
  body: string
}) {
  return (
    <article className="clinic-belief-card">
      <h3 className="clinic-belief-title">{title}</h3>
      <p className="clinic-belief-body">{body}</p>
    </article>
  )
}
