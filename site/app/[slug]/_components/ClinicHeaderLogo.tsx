'use client'

import Image from 'next/image'
import { useState } from 'react'

import { clinicLogoPresentation } from '@/lib/image-policy'

interface Props {
  readonly hospitalName: string
  readonly logoUrl: string | null | undefined
}

export function ClinicHeaderLogo({ hospitalName, logoUrl }: Props) {
  const presentation = clinicLogoPresentation(logoUrl)
  const [failed, setFailed] = useState(presentation.kind === 'fallback')

  if (failed || presentation.kind === 'fallback') {
    return <span className="clinic-header-brand-name">{hospitalName}</span>
  }

  return (
    <Image
      src={presentation.src}
      alt={`${hospitalName} 로고`}
      width={160}
      height={48}
      className="clinic-header-brand-logo"
      onError={() => setFailed(true)}
    />
  )
}
