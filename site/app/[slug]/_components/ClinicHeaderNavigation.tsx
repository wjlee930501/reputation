'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

import { getClinicNavigation } from '@/lib/clinic-design'

import { ExternalIcon } from './icons'

interface Props {
  readonly hospitalRootUrl: string
  readonly websiteUrl: string | null
  readonly variant: 'desktop' | 'mobile'
}

export function ClinicHeaderNavigation({ hospitalRootUrl, websiteUrl, variant }: Props) {
  const pathname = usePathname()
  const navigation = getClinicNavigation(hospitalRootUrl, pathname)
  const navItems = (
    <>
      {navigation.map(({ href, label, ariaCurrent }) => (
        <Link key={href} href={href} aria-current={ariaCurrent}>{label}</Link>
      ))}
      {websiteUrl ? (
        <a
          className="clinic-header-external-link"
          href={websiteUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          공식 홈페이지
          <ExternalIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
        </a>
      ) : null}
    </>
  )

  return (
    <nav
      className={variant === 'desktop' ? 'clinic-header-nav' : 'clinic-header-nav-mobile'}
      aria-label={variant === 'desktop' ? '병원 섹션' : '병원 섹션 (모바일)'}
    >
      {navItems}
    </nav>
  )
}
