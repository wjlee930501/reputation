import type { SVGProps } from 'react'

const baseProps: SVGProps<SVGSVGElement> = {
  className: 'clinic-icon',
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

function withDefaults(props: SVGProps<SVGSVGElement>): SVGProps<SVGSVGElement> {
  return { ...baseProps, ...props, className: props.className ?? baseProps.className }
}

export function PhoneIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...withDefaults(props)}>
      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
    </svg>
  )
}

export function MapPinIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...withDefaults(props)}>
      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
      <circle cx="12" cy="10" r="3" />
    </svg>
  )
}

export function ClockIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...withDefaults(props)}>
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  )
}

export function GlobeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...withDefaults(props)}>
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  )
}

export function ExternalIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...withDefaults(props)}>
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <polyline points="15 3 21 3 21 9" />
      <line x1="10" y1="14" x2="21" y2="3" />
    </svg>
  )
}

export function ChevronRightIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...withDefaults(props)}>
      <polyline points="9 18 15 12 9 6" />
    </svg>
  )
}

export function MessageIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...withDefaults(props)}>
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  )
}

export function StethoscopeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...withDefaults(props)}>
      <path d="M4.5 3H3v5a5 5 0 0 0 10 0V3h-1.5" />
      <path d="M8 13v2a5 5 0 0 0 10 0v-1" />
      <circle cx="19" cy="11" r="2.5" />
    </svg>
  )
}

export function NavigationIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...withDefaults(props)}>
      <polygon points="3 11 22 2 13 21 11 13 3 11" />
    </svg>
  )
}

export function CalendarIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...withDefaults(props)}>
      <rect x="3" y="4" width="18" height="17" rx="2" />
      <line x1="3" y1="9" x2="21" y2="9" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="16" y1="2" x2="16" y2="6" />
    </svg>
  )
}

export function CheckIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...withDefaults(props)}>
      <polyline points="20 6 9 17 4 12" />
    </svg>
  )
}
