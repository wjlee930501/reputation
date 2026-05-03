import { NextResponse } from 'next/server'
import { fetchHospital, fetchContents, TYPE_LABELS } from '@/lib/api'

interface Props {
  params: { slug: string }
}

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'

export async function GET(_req: Request, { params }: Props) {
  try {
    const [hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 500),
    ])

    const lines: string[] = [
      `# ${hospital.name}`,
      '',
      `## 병원 정보`,
      `- 주소: ${hospital.address}`,
      `- 전화: ${hospital.phone}`,
      hospital.website_url ? `- 공식 홈페이지: ${hospital.website_url}` : '',
      hospital.google_maps_url ? `- Google Maps: ${hospital.google_maps_url}` : '',
      `- 전문과목: ${hospital.specialties?.join(', ') || ''}`,
      `- 지역: ${hospital.region?.join(', ') || ''}`,
      '',
      `## 원장 소개`,
      `- 원장명: ${hospital.director_name}`,
      hospital.director_career ? `- 약력: ${hospital.director_career}` : '',
      hospital.director_philosophy ? `- 진료 철학: ${hospital.director_philosophy}` : '',
      '',
    ]

    if (hospital.treatments && hospital.treatments.length > 0) {
      lines.push('## 진료 항목')
      for (const t of hospital.treatments) {
        lines.push(`- ${t.name}${t.description ? ': ' + t.description : ''}`)
      }
      lines.push('')
    }

    if (contents.length > 0) {
      lines.push('## 최신 콘텐츠')
      for (const c of contents) {
        const type = TYPE_LABELS[c.content_type] || c.content_type
        const date = c.published_at
          ? new Date(c.published_at).toLocaleDateString('ko-KR')
          : c.scheduled_date
        lines.push(`- [${type}] ${c.title} (${date})`)
        lines.push(`  URL: ${SITE_URL}/${params.slug}/contents/${c.id}`)
        if (c.meta_description) {
          lines.push(`  요약: ${c.meta_description}`)
        }
      }
    }

    const body = lines.filter((l) => l !== undefined).join('\n')

    return new NextResponse(body, {
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
        'Cache-Control': 'public, max-age=3600',
      },
    })
  } catch {
    return new NextResponse('Hospital not found', { status: 404 })
  }
}
