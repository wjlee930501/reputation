import type { SVGProps } from 'react'

/*
 * 브랜드 일러스트 세트 — 전부 직접 작성한 인라인 SVG(외부 아이콘 패키지 미사용).
 * 규칙:
 *  - 추상 기하 라인만 사용(장기·신체 직접 묘사 금지). 부드러운 곡선·동심원·격자.
 *  - 2색 체계: 주선 currentColor(딥블루 #0b53b8 컨텍스트에서 상속) + 보조선 var(--brand-ink-2).
 *  - 전부 aria-hidden, viewBox 정리, focusable=false. 이모지·그라디언트 없음.
 */

const brandBase: SVGProps<SVGSVGElement> = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
  focusable: false,
}

const INK2 = 'var(--brand-ink-2, #c3d0e4)'

/* ── 히어로 추상 의료 라인 아트 ─────────────────────────────────────
   동심 아크(맥동 링) + 흐르는 곡선(펄스) + 옅은 도트 격자. 딥블루/블루그레이 2색.
   시각 앵커: 병원 히어로 우측 배경에 크게 깔린다. */
export function HeroLineArt({ className, style }: { className?: string; style?: React.CSSProperties }) {
  const dots: Array<[number, number]> = []
  for (let r = 0; r < 5; r++) {
    for (let c = 0; c < 6; c++) {
      dots.push([40 + c * 30, 250 + r * 26])
    }
  }
  return (
    <svg
      {...brandBase}
      viewBox="0 0 420 420"
      className={className}
      style={style}
      preserveAspectRatio="xMidYMid meet"
    >
      {/* 옅은 도트 격자 (보조색) */}
      <g stroke="none" fill={INK2} opacity="0.5">
        {dots.map(([x, y], i) => (
          <circle key={i} cx={x} cy={y} r="1.6" />
        ))}
      </g>
      {/* 동심 아크 — 우상단 링 (주색) */}
      <g strokeWidth="1.6">
        <path d="M300 40a132 132 0 0 1 80 122" opacity="0.9" />
        <path d="M300 84a92 92 0 0 1 60 84" opacity="0.7" />
        <path d="M300 128a52 52 0 0 1 38 46" opacity="0.55" />
      </g>
      {/* 동심원 — 보조 (블루그레이) */}
      <g stroke={INK2} strokeWidth="1.4">
        <circle cx="150" cy="150" r="96" opacity="0.7" />
        <circle cx="150" cy="150" r="62" opacity="0.55" />
      </g>
      <circle cx="150" cy="150" r="26" stroke="currentColor" strokeWidth="1.6" opacity="0.85" />
      <circle cx="150" cy="150" r="3.4" fill="currentColor" stroke="none" />
      {/* 흐르는 펄스 곡선 (주색) */}
      <path
        d="M12 322 H120 l14 -34 16 64 18 -104 20 150 14 -76 h196"
        strokeWidth="1.8"
        opacity="0.9"
      />
    </svg>
  )
}

/* ── 진료철학 원칙용 미니 라인 아이콘 3종 ──────────────────────────── */

/* 검사 — 돋보기 + 확인 라인(추상) */
export function IconExam(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...brandBase} viewBox="0 0 32 32" strokeWidth={1.6} {...props}>
      <circle cx="14" cy="14" r="8.5" />
      <line x1="20.2" y1="20.2" x2="27" y2="27" />
      <path d="M10.5 14.2l2.4 2.4 4.6-5.2" stroke={INK2} />
    </svg>
  )
}

/* 설명 — 대화 패널 + 안내 라인(추상) */
export function IconExplain(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...brandBase} viewBox="0 0 32 32" strokeWidth={1.6} {...props}>
      <path d="M5 8.5a2.5 2.5 0 0 1 2.5-2.5h13A2.5 2.5 0 0 1 23 8.5v8a2.5 2.5 0 0 1-2.5 2.5H12l-4.5 4v-4H7.5A2.5 2.5 0 0 1 5 16.5z" />
      <line x1="9" y1="10.5" x2="19" y2="10.5" stroke={INK2} />
      <line x1="9" y1="14" x2="16" y2="14" stroke={INK2} />
      <path d="M24 22.5l3.5 3.5" opacity="0" />
    </svg>
  )
}

/* 사후관리 — 방패 + 맥동 라인(추상, 지속 관리) */
export function IconAftercare(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...brandBase} viewBox="0 0 32 32" strokeWidth={1.6} {...props}>
      <path d="M16 4.5l9 3.4v6.6c0 6-3.9 10-9 12-5.1-2-9-6-9-12V7.9z" />
      <path d="M11 16.2h2.6l1.6-3.4 2 6.2 1.5-2.8H21" stroke={INK2} />
    </svg>
  )
}

/* ── 콘텐츠 유형별 커버 모티프 7종(추상 기하 라인) ──────────────────
   카드/피처드/아티클 배경 액센트로 사용. 전부 currentColor + 보조색. */

function FaqMotif() {
  return (
    <>
      <rect x="10" y="16" width="46" height="30" rx="8" />
      <path d="M24 46l-4 9 12-9" />
      <rect x="44" y="34" width="40" height="26" rx="7" stroke={INK2} />
      <path d="M70 60l4 8-11-8" stroke={INK2} />
      <line x1="20" y1="27" x2="44" y2="27" stroke={INK2} />
      <line x1="20" y1="35" x2="36" y2="35" stroke={INK2} />
    </>
  )
}
function DiseaseMotif() {
  return (
    <>
      <circle cx="48" cy="40" r="34" stroke={INK2} />
      <circle cx="48" cy="40" r="23" />
      <circle cx="48" cy="40" r="12" stroke={INK2} />
      <circle cx="48" cy="40" r="3" fill="currentColor" stroke="none" />
    </>
  )
}
function TreatmentMotif() {
  return (
    <>
      <path d="M14 62v-14M36 62v-28M58 62v-40M80 62v-24" strokeWidth="1.8" />
      <circle cx="14" cy="44" r="4" />
      <circle cx="36" cy="30" r="4" stroke={INK2} />
      <circle cx="58" cy="18" r="4" />
      <circle cx="80" cy="34" r="4" stroke={INK2} />
      <path d="M14 44l22-14 22-12 22 16" stroke={INK2} />
    </>
  )
}
function ColumnMotif() {
  return (
    <>
      <path d="M20 66L58 20a8 8 0 0 1 12 10L34 74l-16 4z" />
      <line x1="30" y1="60" x2="60" y2="26" stroke={INK2} />
      <path d="M18 78l6-2" />
    </>
  )
}
function HealthMotif() {
  return (
    <>
      <path d="M8 50c12 0 12-22 24-22s12 22 24 22 12-22 24-22" strokeWidth="1.8" />
      <path d="M8 64c12 0 12-16 24-16s12 16 24 16 12-16 24-16" stroke={INK2} />
    </>
  )
}
function LocalMotif() {
  return (
    <>
      <g stroke={INK2}>
        <line x1="14" y1="24" x2="82" y2="24" />
        <line x1="14" y1="44" x2="82" y2="44" />
        <line x1="34" y1="14" x2="34" y2="66" />
        <line x1="62" y1="14" x2="62" y2="66" />
      </g>
      <path d="M48 20a14 14 0 0 1 14 14c0 10-14 22-14 22s-14-12-14-22a14 14 0 0 1 14-14z" strokeWidth="1.8" />
      <circle cx="48" cy="34" r="5" />
    </>
  )
}
function NoticeMotif() {
  return (
    <>
      <path d="M48 16a16 16 0 0 1 16 16c0 12 4 16 6 20H26c2-4 6-8 6-20a16 16 0 0 1 16-16z" />
      <path d="M42 60a6 6 0 0 0 12 0" stroke={INK2} />
      <line x1="48" y1="10" x2="48" y2="16" stroke={INK2} />
    </>
  )
}

const MOTIFS: Record<string, () => JSX.Element> = {
  FAQ: FaqMotif,
  DISEASE: DiseaseMotif,
  TREATMENT: TreatmentMotif,
  COLUMN: ColumnMotif,
  HEALTH: HealthMotif,
  LOCAL: LocalMotif,
  NOTICE: NoticeMotif,
}

/* 콘텐츠 유형 커버 모티프. 이미지가 없을 때 빈 회색 박스 대신 유형별 추상 모티프를 노출. */
export function ContentMotif({
  type,
  className,
  style,
}: {
  type: string
  className?: string
  style?: React.CSSProperties
}) {
  const Motif = MOTIFS[type] ?? FaqMotif
  return (
    <svg
      {...brandBase}
      viewBox="0 0 96 84"
      className={className}
      style={style}
      preserveAspectRatio="xMidYMid meet"
    >
      <Motif />
    </svg>
  )
}
