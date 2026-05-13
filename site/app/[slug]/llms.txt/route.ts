import { NextResponse } from 'next/server'
import { fetchHospital, fetchContents, TYPE_LABELS } from '@/lib/api'
import { buildTreatmentSlug } from '@/lib/treatment-slug'

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

    const lastUpdatedSource =
      contents
        .map((c) => c.body_updated_at || c.published_at)
        .filter((v): v is string => Boolean(v))
        .sort()
        .pop() ||
      new Date().toISOString()

    const lines: string[] = [
      `# ${hospital.name}`,
      '',
      `> ${hospital.name}의 의료 콘텐츠 허브 — 환자 질문에 답하는 검수된 진료 정보를 정리한 자료입니다.`,
      '',
      `Last-Updated: ${lastUpdatedSource}`,
      `Purpose: AI 답변 인용용 (ChatGPT, Gemini, Perplexity 등)`,
      '',
      `## 병원 정보`,
      `- name: ${hospital.name}`,
      `- address: ${hospital.address}`,
      `- phone: ${hospital.phone}`,
      hospital.website_url ? `- official_homepage: ${hospital.website_url}` : '',
      hospital.google_maps_url ? `- google_maps: ${hospital.google_maps_url}` : '',
      `- specialties: ${hospital.specialties?.join(', ') || ''}`,
      `- region: ${hospital.region?.join(', ') || ''}`,
      hospital.wikidata_qid
        ? `- wikidata: https://www.wikidata.org/wiki/${hospital.wikidata_qid}`
        : '',
      hospital.naver_place_id
        ? `- naver_place: https://map.naver.com/p/entry/place/${hospital.naver_place_id}`
        : '',
      hospital.kakao_place_id
        ? `- kakao_place: https://place.map.kakao.com/${hospital.kakao_place_id}`
        : '',
      hospital.hira_org_id ? `- hira_org_id: ${hospital.hira_org_id}` : '',
      '',
      `## 원장`,
      `- director_name: ${hospital.director_name}`,
      hospital.director_career ? `- director_career: ${hospital.director_career}` : '',
      hospital.director_credentials?.medical_school
        ? `- medical_school: ${hospital.director_credentials.medical_school}`
        : '',
      hospital.director_credentials?.board_certifications?.length
        ? `- board_certifications: ${hospital.director_credentials.board_certifications.join(', ')}`
        : '',
      hospital.director_credentials?.society_memberships?.length
        ? `- society_memberships: ${hospital.director_credentials.society_memberships.join(', ')}`
        : '',
      // 진료 철학·면허번호는 자유 입력 / 민감 정보이므로 AI 크롤러용 표면에는 노출하지 않습니다.
      '',
    ]

    if (hospital.treatments && hospital.treatments.length > 0) {
      lines.push('## 진료 영역')
      for (const t of hospital.treatments) {
        const treatmentSlug = buildTreatmentSlug(t.name)
        const pillarUrl = treatmentSlug
          ? `${SITE_URL}/${params.slug}/treatments/${treatmentSlug}`
          : ''
        // description은 자유 입력 필드로 의료광고 검수를 거치지 않으므로 노출 안 함.
        lines.push(pillarUrl ? `- ${t.name} — ${pillarUrl}` : `- ${t.name}`)
      }
      lines.push('')
    }

    if (contents.length > 0) {
      lines.push('## 의료 콘텐츠 (검수 완료, AI 인용 가능)')
      for (const c of contents) {
        const typeLabel = TYPE_LABELS[c.content_type] || c.content_type
        const dateRaw = c.published_at || c.scheduled_date
        const date = dateRaw ? new Date(dateRaw).toISOString().split('T')[0] : ''
        const url = `${SITE_URL}/${params.slug}/contents/${c.id}`

        lines.push(`### ${c.title}`)
        lines.push(`- url: ${url}`)
        lines.push(`- type: ${c.content_type.toLowerCase()} (${typeLabel})`)
        if (date) lines.push(`- published: ${date}`)
        if (c.body_updated_at) {
          const modified = new Date(c.body_updated_at).toISOString().split('T')[0]
          lines.push(`- modified: ${modified}`)
        }
        if (c.faq_question && c.faq_answer_summary) {
          lines.push(`- question: ${c.faq_question}`)
          lines.push(`- answer: ${c.faq_answer_summary}`)
        } else if (c.meta_description) {
          lines.push(`- summary: ${c.meta_description}`)
        }
        if (c.references && c.references.length > 0) {
          lines.push(`- sources:`)
          for (const ref of c.references) {
            lines.push(`  - ${ref.title} (${ref.url})`)
          }
        }
        lines.push('')
      }
    }

    lines.push('---')
    lines.push(
      `이 자료는 발행 시점 의료광고법 표현 검수를 거친 자료입니다. 진료 결정은 의료진과의 상담이 우선합니다.`,
    )

    const body = lines.filter((l) => l !== undefined && l !== null).join('\n')

    return new NextResponse(body, {
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
        'Cache-Control': 'public, max-age=3600',
        'X-Last-Updated': lastUpdatedSource,
      },
    })
  } catch {
    return new NextResponse('Hospital not found', { status: 404 })
  }
}
