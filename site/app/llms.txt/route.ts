import { NextResponse } from 'next/server'

import { getApiBase } from '@/lib/config'

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'

interface HospitalEntry {
  slug: string
  aeo_domain?: string | null
}

export async function GET() {
  const apiBase = getApiBase(false)
  const lines = ['# Re:putation AEO Hospital Index', '']

  if (!apiBase) {
    return new NextResponse(lines.join('\n'), {
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    })
  }

  try {
    const res = await fetch(`${apiBase}/hospitals`, { next: { revalidate: 3600 } })
    if (res.ok) {
      const hospitals: HospitalEntry[] = await res.json()
      for (const hospital of hospitals) {
        lines.push(`- ${SITE_URL}/${hospital.slug}`)
        lines.push(`  llms: ${SITE_URL}/${hospital.slug}/llms.txt`)
      }
    }
  } catch {
    lines.push('- Hospital index temporarily unavailable')
  }

  return new NextResponse(lines.join('\n'), {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'public, max-age=3600',
    },
  })
}
