import Link from 'next/link'
import type { ReactNode } from 'react'
import { SectionLabel } from './SectionLabel'
import { LineBroken } from './LineBroken'
import { lineBreaksFor, type ClinicSiteConfig } from '@/lib/clinic-site-config'

import { IconAftercare, IconExam, IconExplain } from '@/components/brand'
import { CARE_PRINCIPLES, CARE_PRINCIPLES_LEDE, resolveCarePrinciples } from '@/lib/clinic-template-copy'
import type { CarePrinciplesCopy } from '@/lib/hospital-payload'
import { regionSentenceLabel } from '@/lib/clinic-region'

interface Props {
  /** 섹션 설정(줄바꿈 지정을 읽는다, `lineBreaksFor`). 없으면 지금 마크업 그대로. */
  siteConfig?: ClinicSiteConfig | null
  /** 영문 소제목(섹션 설정·레이아웃 기본, `plan.label`). 없으면 그리지 않는다 — 지금 마크업 그대로. */
  sectionLabel?: string | null
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
  /** false면 병원이 쓴 카드만(섹션 설정 `care_principles.use_default_cards`). 기본은 지금처럼 빈 자리를 기본 카드로. */
  useDefaultCards?: boolean
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
  useDefaultCards = true,
  channelsHref,
  sectionLabel = null,
  siteConfig = null,
}: Props) {
  const principles = resolveCarePrinciples(custom)
  const own = useDefaultCards ? principles.cards : principles.cards.filter((_, index) => Boolean(custom?.cards[index]))
  const cards = cardCount ? own.slice(0, cardCount) : own
  // 기본 카드를 끄고 병원 카드가 없으면 섹션을 그리지 않는다(빈 카드 칸이 오른쪽 열을 차지하지 않게).
  if (cards.length === 0) return null
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
            {/* 영문 소제목은 우리말 머리글 위 — 'POLICY / 진료 원칙 / 제목'(레퍼런스 3-라: 영문 + 작은 우리말). */}
            {sectionLabel ? <SectionLabel text={sectionLabel} /> : null}
            <span className="clinic-principles-eyebrow">진료 원칙</span>
            <h2 className="clinic-section-title">
              <LineBroken
                text={title ?? principles.title}
                breaks={lineBreaksFor(siteConfig, 'care_principles.title', title ?? principles.title)}
              />
            </h2>
            <p className="clinic-principles-statement">
              <LineBroken text={lede} breaks={lineBreaksFor(siteConfig, 'care_principles.lede', lede)} />
            </p>
          </header>

          <div className="clinic-belief-grid">
            {cards.map((principle) => {
              // 줄바꿈 경로의 카드 번호는 카드 자리(기본 카드를 끄거나 카드 수를 줄여도 그 자리 번호)다.
              const slot = principles.cards.indexOf(principle)
              return (
                <BeliefCard
                  key={principle.key}
                  icon={BELIEF_ICONS[principle.key]}
                  title={
                    <LineBroken
                      text={principle.title}
                      breaks={lineBreaksFor(siteConfig, `care_principles.cards[${slot}].title`, principle.title)}
                    />
                  }
                  body={
                    <LineBroken
                      text={principle.body}
                      breaks={lineBreaksFor(siteConfig, `care_principles.cards[${slot}].body`, principle.body)}
                    />
                  }
                />
              )
            })}
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
  title: ReactNode
  body: ReactNode
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
