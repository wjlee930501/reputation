import Link from 'next/link'
import type { ReactNode } from 'react'

import { IconAftercare, IconExam, IconExplain } from './brand'

interface Props {
  hospitalRootUrl: string
  hospitalName: string
  specialties: string[]
  region: string[]
  // 승인·검수된 공개 서사가 있으면 리드 문장으로 사용. 없으면 프로파일 기반 사실형 문장으로 대체.
  publicAbout?: string | null
}

// 백엔드가 오염된 public_about을 None으로 강등하기 전이라도, 내부 파이프라인 언어가
// 환자 표면에 새지 않도록 프론트에서 한 번 더 방어한다(백엔드 배포 타이밍에 비의존).
// 영문 내부 용어(검색 최적화 약어 등)는 백엔드 _vetted_public_about 게이트가 차단한다 —
// 여기는 실제 사고에서 관측된 파이프라인 문구와 크롤러(Jina) 시그니처만 다룬다.
const INTERNAL_LANGUAGE_MARKERS = [
  '자료에서 확인된',
  '핵심 메시지',
  '근거 범위',
  '말할 수 있는 약속',
  'Title:',
  '검색 시스템',
  '브랜드 구조',
]

function sanitizePublicAbout(value: string | null | undefined): string | null {
  const trimmed = value?.trim()
  if (!trimmed) return null
  if (INTERNAL_LANGUAGE_MARKERS.some((marker) => trimmed.includes(marker))) return null
  return trimmed
}

/* 임상적 주장(치료 순서·수술 여부 등)을 만들어 원장 발언처럼 노출하지 않는다.
   여기서는 안내·설명·상담 방식에 대한 비임상 운영 원칙만 다룬다. */
export function CarePrinciples({ hospitalRootUrl, hospitalName, specialties, region, publicAbout }: Props) {
  const specialtyText = specialties.filter(Boolean).join(', ')
  const regionText = region.filter(Boolean).join(' ')

  // 검수 통과한 깨끗한 서사만 리드로 사용. 오염·부재 시 프로파일 기반 사실형 문장으로 대체해
  // 이 섹션이 언제나 자연스럽게 보이도록 한다.
  const lede =
    sanitizePublicAbout(publicAbout) ||
    [
      regionText && specialtyText
        ? `${regionText}에서 ${specialtyText} 진료를 맡고 있는 ${hospitalName}입니다.`
        : `${hospitalName}의 진료 안내입니다.`,
      '증상의 원인을 먼저 확인하고, 검사와 치료 과정을 이해할 수 있게 설명한 뒤 진행합니다.',
    ]
      .filter(Boolean)
      .join(' ')

  return (
    <section className="clinic-section clinic-section--principles">
      <div className="clinic-section-inner">
        <div className="clinic-principles-band">
          <header className="clinic-principles-lead">
            <span className="clinic-principles-eyebrow">진료 원칙</span>
            <h2 className="clinic-section-title">설명과 상담을 우선합니다</h2>
            <p className="clinic-principles-statement">{lede}</p>
          </header>

          <div className="clinic-belief-grid">
            <BeliefCard
              icon={<IconExplain />}
              title="충분히 설명합니다"
              body="검사와 진료 과정을 환자가 이해할 수 있는 언어로 설명하고, 궁금한 점을 확인한 뒤 진행합니다."
            />
            <BeliefCard
              icon={<IconExam />}
              title="확인된 정보만 안내합니다"
              body="이 페이지의 진료 안내와 의료 정보는 출처와 업데이트 일자를 함께 표기하며, 과장된 표현을 사용하지 않습니다."
            />
            <BeliefCard
              icon={<IconAftercare />}
              title="상담으로 함께 결정합니다"
              body="치료 방향은 글이 아니라 진료실에서의 상담과 개인별 상태 확인을 거쳐 결정됩니다."
            />
          </div>

          <div className="clinic-principles-actions">
            <Link href={`${hospitalRootUrl}/doctor`}>의료진 보기</Link>
            <Link href={`${hospitalRootUrl}/contents`}>전체 글 보기</Link>
            <Link href={`${hospitalRootUrl}#contact`}>공식 채널 보기</Link>
          </div>
        </div>
      </div>
    </section>
  )
}

function BeliefCard({
  icon,
  title,
  body,
}: {
  icon: ReactNode
  title: string
  body: string
}) {
  return (
    <article className="clinic-belief-card">
      <span className="clinic-belief-icon" aria-hidden="true">{icon}</span>
      <div className="clinic-belief-copy">
        <h3 className="clinic-belief-title">{title}</h3>
        <p className="clinic-belief-body">{body}</p>
      </div>
    </article>
  )
}
