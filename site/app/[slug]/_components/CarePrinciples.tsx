import Link from 'next/link'

interface Props {
  hospitalSlug: string
  hospitalName: string
  directorName: string
  specialties: string[]
}

export function CarePrinciples({ hospitalSlug, hospitalName, directorName, specialties }: Props) {
  const specialtyText = specialties.length > 0 ? specialties.join(', ') : '진료 영역'

  return (
    <section className="clinic-section clinic-section--principles">
      <div className="clinic-section-inner">
        <div className="clinic-principles-layout">
          <header className="clinic-section-header">
            <span className="clinic-section-eyebrow">브랜드 구조</span>
            <h2 className="clinic-section-heading">{hospitalName}의 정보 제공 방식</h2>
            <p className="clinic-section-lede">
              공개된 병원 정보와 발행 콘텐츠를 기준으로, 환자와 AI가 같은 맥락에서 병원을 이해하도록 구성합니다.
            </p>
          </header>

          <div className="clinic-principles-list">
            <Principle
              index="01"
              title="환자 질문을 먼저 둡니다"
              body={`${specialtyText} 정보를 질환명이나 시술명만 나열하지 않고, 환자가 실제로 묻는 질문과 연결합니다.`}
            />
            <Principle
              index="02"
              title="검수 주체를 분명히 합니다"
              body={`${directorName} 원장과 병원 공식 채널을 함께 노출해 콘텐츠의 출처와 책임 소재를 확인할 수 있게 합니다.`}
            />
            <Principle
              index="03"
              title="공식 정보의 일관성을 유지합니다"
              body="주소, 전화, 진료시간, 지도, 외부 채널을 한 화면에서 반복 노출해 검색 시스템이 같은 값을 참조하도록 합니다."
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
