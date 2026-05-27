import Link from 'next/link'

interface Props {
  hospitalSlug: string
  directorName: string
  specialties: string[]
}

export function CarePrinciples({ hospitalSlug, directorName, specialties }: Props) {
  const specialtyText = specialties.length > 0 ? specialties.join(', ') : '진료 영역'

  return (
    <section className="clinic-section clinic-section--principles">
      <div className="clinic-section-inner">
        <div className="clinic-principles-layout">
          <header className="clinic-section-header">
            <span className="clinic-section-eyebrow">진료 정보 기준</span>
            <h2 className="clinic-section-heading">수술부터 결정하지 않습니다</h2>
            <p className="clinic-section-lede">
              통증은 같은 부위라도 원인이 다를 수 있습니다. 진찰과 검사 소견을 함께 확인한 뒤
              비수술 치료, 재활, 추가 검사 필요성을 단계적으로 상담합니다.
            </p>
          </header>

          <div className="clinic-principles-list">
            <Principle
              index="01"
              title="정확한 진단"
              body={`${specialtyText} 진료에서는 통증 위치, 움직임 제한, 근력, 보행과 생활 습관을 함께 확인합니다.`}
            />
            <Principle
              index="02"
              title="영상검사 확인"
              body="필요한 경우 X-ray, 초음파 등 검사 소견과 실제 증상이 일치하는지 확인합니다."
            />
            <Principle
              index="03"
              title="단계적 치료와 재활"
              body={`${directorName} 원장 진료 분야를 기준으로 약물·주사·물리치료·도수재활 등 선택지를 설명합니다.`}
            />
          </div>
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

function Principle({
  index,
  title,
  body,
}: {
  index: string
  title: string
  body: string
}) {
  return (
    <article className="clinic-principle-card">
      <span>{index}</span>
      <div>
        <h3>{title}</h3>
        <p>{body}</p>
      </div>
    </article>
  )
}
