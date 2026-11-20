import Link from 'next/link'
import type { ReactNode } from 'react'

import { IconAftercare, IconExam, IconExplain } from '@/components/brand'
import { CARE_PRINCIPLES, CARE_PRINCIPLES_LEDE, resolveCarePrinciples } from '@/lib/clinic-template-copy'
import type { CarePrinciplesCopy } from '@/lib/hospital-payload'
import { regionSentenceLabel } from '@/lib/clinic-region'

interface Props {
  hospitalRootUrl: string
  hospitalName: string
  specialties: string[]
  region: string[]
  // 승인·검수된 공개 서사가 있으면 리드 문장으로 사용. 없으면 프로파일 기반 사실형 문장으로 대체.
  publicAbout?: string | null
  /** 병원마다 바꾼 진료 원칙 문구. 소개 문장은 승인된 공개 서사보다 앞선다(마케터가 직접 쓴 문장). */
  custom?: CarePrinciplesCopy | null
  /** 섹션 설정이 바꾼 제목(레이아웃 편집기). 없으면 병원 정보의 제목·공통 제목. */
  title?: string | null
  /** 보일 카드 수(섹션 설정). 없으면 전부. */
  cardCount?: number | null
  /** '공식 채널 보기'가 가는 곳. 오시는 길 섹션이 꺼진 홈은 하위 페이지로 보낸다. */
  channelsHref?: string
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

const BELIEF_ICONS: Record<(typeof CARE_PRINCIPLES)[number]['key'], ReactNode> = {
  explain: <IconExplain />,
  verified: <IconExam />,
  decide: <IconAftercare />,
}

/* 임상적 주장(치료 순서·수술 여부 등)을 만들어 원장 발언처럼 노출하지 않는다.
   원칙 문구는 lib/clinic-template-copy.ts 한 곳에 있다. */
export function CarePrinciples({
  hospitalRootUrl,
  hospitalName,
  specialties,
  region,
  publicAbout,
  custom = null,
  title,
  cardCount,
  channelsHref,
}: Props) {
  const principles = resolveCarePrinciples(custom)
  const cards = cardCount ? principles.cards.slice(0, cardCount) : principles.cards
  const specialtyText = specialties.filter(Boolean).join(', ')
  const regionText = regionSentenceLabel(region)

  // 검수 통과한 깨끗한 서사만 리드로 사용. 오염·부재 시 프로파일 기반 사실형 문장으로 대체해
  // 이 섹션이 언제나 자연스럽게 보이도록 한다.
  const lede =
    custom?.lede ||
    sanitizePublicAbout(publicAbout) ||
    [
      regionText && specialtyText
        ? `${regionText}에서 ${specialtyText} 진료를 맡고 있는 ${hospitalName}입니다.`
        : `${hospitalName}의 진료 안내입니다.`,
      CARE_PRINCIPLES_LEDE,
    ]
      .filter(Boolean)
      .join(' ')

  return (
    <section className="clinic-section clinic-section--principles">
      <div className="clinic-section-inner">
        <div className="clinic-principles-band">
          <header className="clinic-principles-lead">
            <span className="clinic-principles-eyebrow">진료 원칙</span>
            <h2 className="clinic-section-title">{title ?? principles.title}</h2>
            <p className="clinic-principles-statement">{lede}</p>
          </header>

          <div className="clinic-belief-grid">
            {cards.map((principle) => (
              <BeliefCard
                key={principle.key}
                icon={BELIEF_ICONS[principle.key]}
                title={principle.title}
                body={principle.body}
              />
            ))}
          </div>

          <div className="clinic-principles-actions">
            <Link href={`${hospitalRootUrl}/doctor`}>의료진 보기</Link>
            <Link href={`${hospitalRootUrl}/contents`}>전체 글 보기</Link>
            <Link href={channelsHref ?? `${hospitalRootUrl}#contact`}>공식 채널 보기</Link>
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
