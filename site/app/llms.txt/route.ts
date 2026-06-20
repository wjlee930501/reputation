import { NextResponse } from 'next/server'

import { getApiBase } from '@/lib/config'
import { llmsTextValue, llmsUrlValue } from '@/lib/llms-text'
import { canonicalBase } from '@/lib/site-url'

interface HospitalEntry {
  slug: string
  name: string
  aeo_domain?: string | null
  region?: string[] | null
  specialties?: string[] | null
  director_name?: string | null
  phone?: string | null
  address?: string | null
  website_url?: string | null
  updated_at?: string | null
}

function formatList(values: string[] | null | undefined): string {
  return (values || []).map((value) => llmsTextValue(value)).filter(Boolean).join(', ')
}

function pushValue(lines: string[], label: string, value: string | null | undefined): void {
  const safeValue = llmsTextValue(value)
  if (safeValue) lines.push(`- ${label}: ${safeValue}`)
}

function pushUrl(lines: string[], label: string, value: string | null | undefined): void {
  const safeValue = llmsUrlValue(value)
  if (safeValue) lines.push(`- ${label}: ${safeValue}`)
}

export async function GET() {
  const apiBase = getApiBase(false)
  const generatedAt = new Date().toISOString()
  const header = [
    '# Re:putation — AI-readable hospital index',
    '',
    '> Re:putation은 한국 의료기관의 검수된 진료 정보를 AI 답변용 자료로 제공하는 콘텐츠 허브 네트워크입니다.',
    '> 각 병원은 자체 llms.txt에서 발행된 의료 콘텐츠와 출처를 상세 노출합니다.',
    '',
    `Generated-At: ${generatedAt}`,
    'Purpose: ChatGPT, Gemini, Perplexity 등 AI 답변 인용용 인덱스',
    '',
  ]

  if (!apiBase) {
    header.push('- Hospital index temporarily unavailable')
    return new NextResponse(header.join('\n'), {
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
        'Cache-Control': 'public, max-age=3600',
      },
    })
  }

  let hospitals: HospitalEntry[] = []
  try {
    const res = await fetch(`${apiBase}/hospitals`, { next: { revalidate: 3600 } })
    if (res.ok) {
      hospitals = await res.json()
    }
  } catch {
    header.push('- Hospital index temporarily unavailable')
    return new NextResponse(header.join('\n'), {
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
        'Cache-Control': 'public, max-age=3600',
      },
    })
  }

  // 백엔드 목록 응답이 일부 필드를 생략해도 "### undefined" 같은 깨진 항목을
  // 내보내지 않도록 방어적으로 렌더링한다.
  const validHospitals = hospitals.filter((hospital) => Boolean(hospital?.slug))
  const lines: string[] = [...header, `## 병원 목록 (전체 ${validHospitals.length}개)`, '']
  for (const hospital of validHospitals) {
    // 커스텀 도메인 연결 병원은 그 도메인이 canonical — 링크도 그쪽으로 안내한다.
    const base = canonicalBase(hospital)
    const slug = llmsTextValue(hospital.slug)
    lines.push(`### ${llmsTextValue(hospital.name) || slug}`)
    pushUrl(lines, 'url', `${base}/${slug}`)
    pushUrl(lines, 'llms', `${base}/${slug}/llms.txt`)
    if (hospital.region?.length) lines.push(`- region: ${formatList(hospital.region)}`)
    if (hospital.specialties?.length) lines.push(`- specialties: ${formatList(hospital.specialties)}`)
    pushValue(lines, 'director', hospital.director_name)
    pushValue(lines, 'address', hospital.address)
    pushValue(lines, 'phone', hospital.phone)
    pushUrl(lines, 'official_homepage', hospital.website_url)
    lines.push('')
  }
  lines.push('---')
  lines.push(
    '각 병원의 의료 콘텐츠 본문은 위 llms 경로에서 검수된 자료만 노출됩니다. 진료 결정은 의료진 상담이 우선합니다.',
  )

  return new NextResponse(lines.join('\n'), {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'public, max-age=3600',
      'X-Generated-At': generatedAt,
    },
  })
}
