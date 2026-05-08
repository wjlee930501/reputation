import type { SVGProps } from 'react'

const baseProps: SVGProps<SVGSVGElement> = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

function Wrap(props: SVGProps<SVGSVGElement>) {
  return { ...baseProps, ...props }
}

export function StethoscopeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <path d="M11 2v4a3 3 0 0 1-6 0V2" />
      <path d="M5 6c0 5 3 9 6 10v3a4 4 0 0 0 8 0v-7" />
      <circle cx="19" cy="11" r="2" />
    </svg>
  )
}

export function HeartIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <path d="M19.5 12.6 12 20l-7.5-7.4a4.6 4.6 0 0 1 6.5-6.5L12 6.7l1-1.1a4.6 4.6 0 0 1 6.5 6.5z" />
    </svg>
  )
}

export function BrainIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 1 4 3 3 0 0 0 4 2 3 3 0 0 0 3-3V5a1 1 0 0 0-1-1z" />
      <path d="M15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-1 4 3 3 0 0 1-4 2 3 3 0 0 1-3-3V5a1 1 0 0 1 1-1z" />
    </svg>
  )
}

export function ToothIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <path d="M12 22c-1.5 0-2-3-2-5 0-1-.5-2-2-2-1.7 0-3-1.3-3-5 0-3 2-7 7-7s7 4 7 7c0 3.7-1.3 5-3 5-1.5 0-2 1-2 2 0 2-.5 5-2 5z" />
    </svg>
  )
}

export function BoneIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <path d="M16.5 4a2.5 2.5 0 0 1 2 4 2.5 2.5 0 0 1-2 4l-9 9a2.5 2.5 0 0 1-4-2 2.5 2.5 0 0 1-4-2 2.5 2.5 0 0 1 2-4 2.5 2.5 0 0 1 2-4l9-9a2.5 2.5 0 0 1 4 2 2.5 2.5 0 0 1 4 2z" />
    </svg>
  )
}

export function LungIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <path d="M12 4v8" />
      <path d="M9 16a4 4 0 0 1-4 4c-2 0-3-2-3-4 0-3 1-7 3-9 1-1 3-1 4 0v3" />
      <path d="M15 16a4 4 0 0 0 4 4c2 0 3-2 3-4 0-3-1-7-3-9-1-1-3-1-4 0v3" />
    </svg>
  )
}

export function EyeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}

export function PillIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <rect x="3" y="9" width="18" height="6" rx="3" transform="rotate(-30 12 12)" />
      <line x1="9.5" y1="14.5" x2="14.5" y2="9.5" />
    </svg>
  )
}

export function MicroscopeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <path d="M6 18h12" />
      <path d="M9 14l4-10 4 4-4 10z" />
      <circle cx="9" cy="18" r="2" />
      <path d="M5 22h14" />
    </svg>
  )
}

export function ClipboardPlusIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <rect x="6" y="4" width="12" height="18" rx="2" />
      <path d="M9 4V2h6v2" />
      <path d="M12 11v6" />
      <path d="M9 14h6" />
    </svg>
  )
}

export function ShieldIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <path d="M12 2 4 6v6c0 5 3 9 8 10 5-1 8-5 8-10V6z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  )
}

export function SyringeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...Wrap(props)}>
      <path d="m17 2 5 5" />
      <path d="m13 6 5 5" />
      <path d="M14 7 7 14a3 3 0 0 0 0 4l-3 3 1 1 3-3a3 3 0 0 0 4 0l7-7z" />
    </svg>
  )
}

type IconComponent = (props: SVGProps<SVGSVGElement>) => JSX.Element

interface IconRule {
  keywords: string[]
  Icon: IconComponent
  hue: 'blue' | 'green' | 'purple' | 'yellow' | 'red' | 'cool'
}

// 진료 항목 이름에 키워드가 포함되면 해당 아이콘으로 매핑.
// 매칭 안 되면 fallback (StethoscopeIcon).
const RULES: IconRule[] = [
  { keywords: ['치아', '치과', '구강', 'tooth', 'dental'], Icon: ToothIcon, hue: 'blue' },
  { keywords: ['심장', '심혈관', 'cardio', 'heart'], Icon: HeartIcon, hue: 'red' },
  { keywords: ['뇌', '신경', '뇌혈관', 'brain', 'neuro'], Icon: BrainIcon, hue: 'purple' },
  { keywords: ['관절', '척추', '정형', '디스크', '뼈', 'bone', 'orthopedic'], Icon: BoneIcon, hue: 'cool' },
  { keywords: ['호흡', '폐', '천식', '기관지', 'lung', 'respiratory'], Icon: LungIcon, hue: 'cool' },
  { keywords: ['안과', '눈', '시력', 'eye', 'vision'], Icon: EyeIcon, hue: 'blue' },
  { keywords: ['약', '처방', '복용', 'pharma', 'prescription'], Icon: PillIcon, hue: 'green' },
  { keywords: ['검사', '진단', '검진', '내시경', 'endoscopy', 'diagnostic'], Icon: MicroscopeIcon, hue: 'purple' },
  { keywords: ['예방', '백신', '면역', 'vaccine', 'immune'], Icon: ShieldIcon, hue: 'green' },
  { keywords: ['주사', '시술', '주입', 'injection', 'procedure'], Icon: SyringeIcon, hue: 'yellow' },
  { keywords: ['수술', 'surgery', 'operation'], Icon: ClipboardPlusIcon, hue: 'red' },
]

export function pickIconForTreatment(name: string): { Icon: IconComponent; hue: IconRule['hue'] } {
  const lower = name.toLowerCase()
  for (const rule of RULES) {
    if (rule.keywords.some((k) => lower.includes(k.toLowerCase()))) {
      return { Icon: rule.Icon, hue: rule.hue }
    }
  }
  return { Icon: StethoscopeIcon, hue: 'cool' }
}
