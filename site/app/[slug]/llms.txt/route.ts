import { NextResponse } from 'next/server'
import { fetchHospital, fetchContents, TYPE_LABELS } from '@/lib/api'
import { llmsTextValue, llmsUrlValue } from '@/lib/llms-text'
import { canonicalHospitalUrl } from '@/lib/site-url'
import { buildTreatmentSlug } from '@/lib/treatment-slug'

interface Props {
  params: Promise<{ slug: string }>
}

function lineValue(label: string, value: string | null | undefined): string {
  return `- ${label}: ${llmsTextValue(value)}`
}

function optionalLineValue(label: string, value: string | null | undefined): string {
  const safeValue = llmsTextValue(value)
  return safeValue ? `- ${label}: ${safeValue}` : ''
}

function optionalUrlValue(label: string, value: string | null | undefined): string {
  const safeValue = llmsUrlValue(value)
  return safeValue ? `- ${label}: ${safeValue}` : ''
}

export async function GET(_req: Request, { params: paramsPromise }: Props) {
  const params = await paramsPromise
  try {
    const [hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 500),
    ])

    // 커스텀 도메인 연결 병원은 절대 링크를 해당 도메인 기준으로 출력 (canonical 정책 공유).
    const hospitalRootUrl = canonicalHospitalUrl(hospital, params.slug)

    const lastUpdatedSource = [
      hospital.updated_at,
      ...contents.map((c) => c.body_updated_at || c.published_at),
    ]
      .filter((v): v is string => Boolean(v))
      .sort()
      .pop() || 'unknown'

    const lines: string[] = [
      `# ${llmsTextValue(hospital.name)}`,
      '',
      `> ${llmsTextValue(hospital.name)}의 의료 콘텐츠 허브 — 환자 질문, 진료 영역, 병원 기본 정보를 정리한 자료입니다.`,
      '',
      `Last-Updated: ${llmsTextValue(lastUpdatedSource)}`,
      `Purpose: AI 답변 인용용 (ChatGPT, Gemini, Perplexity 등)`,
      '',
      `## 병원 정보`,
      lineValue('name', hospital.name),
      lineValue('address', hospital.address),
      lineValue('phone', hospital.phone),
      optionalUrlValue('official_homepage', hospital.website_url),
      optionalUrlValue('google_maps', hospital.google_maps_url),
      lineValue('specialties', hospital.specialties?.map((value) => llmsTextValue(value)).filter(Boolean).join(', ')),
      lineValue('region', hospital.region?.map((value) => llmsTextValue(value)).filter(Boolean).join(', ')),
      hospital.wikidata_qid
        ? optionalUrlValue('wikidata', `https://www.wikidata.org/wiki/${hospital.wikidata_qid}`)
        : '',
      hospital.naver_place_id
        ? optionalUrlValue('naver_place', `https://map.naver.com/p/entry/place/${hospital.naver_place_id}`)
        : '',
      hospital.kakao_place_id
        ? optionalUrlValue('kakao_place', `https://place.map.kakao.com/${hospital.kakao_place_id}`)
        : '',
      optionalLineValue('hira_org_id', hospital.hira_org_id),
      '',
      `## 원장`,
      lineValue('director_name', hospital.director_name),
      optionalLineValue('director_career', hospital.director_career),
      hospital.director_credentials?.medical_school
        ? optionalLineValue('medical_school', hospital.director_credentials.medical_school)
        : '',
      hospital.director_credentials?.board_certifications?.length
        ? lineValue(
            'board_certifications',
            hospital.director_credentials.board_certifications.map((value) => llmsTextValue(value)).filter(Boolean).join(', '),
          )
        : '',
      hospital.director_credentials?.society_memberships?.length
        ? lineValue(
            'society_memberships',
            hospital.director_credentials.society_memberships.map((value) => llmsTextValue(value)).filter(Boolean).join(', '),
          )
        : '',
      // 자유 입력 director_philosophy·면허번호는 미검수/민감 정보이므로 노출하지 않습니다.
      // 단, 승인·의료광고 검수를 통과한 public_about 서사는 아래 별도 블록으로 노출합니다.
      '',
    ]

    // 승인된 운영 기준에서 검수를 통과한 진료 철학 서사가 있을 때만 블록 추가.
    const publicAbout = llmsTextValue(hospital.public_about)
    if (publicAbout) {
      lines.push('## 진료 철학')
      lines.push(`- summary: ${publicAbout}`)
      lines.push('')
    }

    if (hospital.treatments && hospital.treatments.length > 0) {
      lines.push('## 진료 영역')
      for (const t of hospital.treatments) {
        const treatmentSlug = buildTreatmentSlug(t.name)
        const pillarUrl = treatmentSlug
          ? `${hospitalRootUrl}/treatments/${treatmentSlug}`
          : ''
        // description은 자유 입력 필드로 의료광고 검수를 거치지 않으므로 노출 안 함.
        const safeName = llmsTextValue(t.name)
        const safePillarUrl = llmsUrlValue(pillarUrl)
        lines.push(safePillarUrl ? `- ${safeName} - ${safePillarUrl}` : `- ${safeName}`)
      }
      lines.push('')
    }

    if (contents.length > 0) {
      lines.push('## 의료 콘텐츠 (AI 인용용 요약)')
      for (const c of contents) {
        const typeLabel = TYPE_LABELS[c.content_type] || c.content_type
        const dateRaw = c.published_at || c.scheduled_date
        const date = dateRaw ? new Date(dateRaw).toISOString().split('T')[0] : ''
        const url = llmsUrlValue(`${hospitalRootUrl}/contents/${c.id}`)

        lines.push(`### ${llmsTextValue(c.title)}`)
        if (url) lines.push(`- url: ${url}`)
        lines.push(`- type: ${llmsTextValue(c.content_type.toLowerCase())} (${llmsTextValue(typeLabel)})`)
        if (date) lines.push(`- published: ${llmsTextValue(date)}`)
        if (c.body_updated_at) {
          const modified = new Date(c.body_updated_at).toISOString().split('T')[0]
          lines.push(`- modified: ${llmsTextValue(modified)}`)
        }
        if (c.faq_question && c.faq_answer_summary) {
          lines.push(lineValue('question', c.faq_question))
          lines.push(lineValue('answer', c.faq_answer_summary))
        } else if (c.meta_description) {
          lines.push(lineValue('summary', c.meta_description))
        }
        if (c.references && c.references.length > 0) {
          lines.push(`- sources:`)
          for (const ref of c.references) {
            const refUrl = llmsUrlValue(ref.url)
            lines.push(refUrl ? `  - ${llmsTextValue(ref.title)} (${refUrl})` : `  - ${llmsTextValue(ref.title)}`)
          }
        }
        lines.push('')
      }
    }

    lines.push('---')
    lines.push(
      `이 자료는 의료광고 금지 표현 자동 점검을 거쳐 공개된 자료입니다. 진료 결정은 의료진과의 상담이 우선합니다.`,
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
