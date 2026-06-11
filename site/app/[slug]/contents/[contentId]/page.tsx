import { Metadata } from 'next'
import Image from 'next/image'
import Link from 'next/link'
import { notFound } from 'next/navigation'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import {
  ContentNotFoundError,
  fetchContent,
  fetchContents,
  fetchHospital,
  HospitalNotFoundError,
  resolveAssetUrl,
  SOURCE_TYPE_LABELS,
  TYPE_LABELS,
} from '@/lib/api'
import { shouldBypassNextImageOptimization } from '@/lib/image-policy'
import { safeExternalHref } from '@/lib/safe-url'
import { buildTreatmentSlug, inferPillarTreatment } from '@/lib/treatment-slug'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../../_components/Breadcrumb'
import { ClinicFooter } from '../../_components/ClinicFooter'
import { ClinicHeader } from '../../_components/ClinicHeader'
import { ExternalIcon } from '../../_components/icons'
import { JsonLd } from '../../_components/JsonLd'

interface Props {
  params: Promise<{ slug: string; contentId: string }>
}

export const revalidate = 3600

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'

// 한국어 평균 읽기 속도 약 600자/분.
const KOREAN_READING_SPEED_CHARS_PER_MIN = 600

// WCAG/Section508 권장: alt 최대 약 125자, 짧고 묘사적이며 "image of" 군더더기 금지.
// Imagen 3로 자동 생성된 일러스트라는 사실은 본문 하단 캡션으로 별도 고지한다.
const ALT_MAX_LENGTH = 125

function buildImageAlt(args: {
  contentTitle: string
  typeLabel: string
  hospitalName: string
  region: string[]
  directorName: string
}): string {
  const regionLabel = args.region?.join(' ') ?? ''
  const parts = [
    `${args.typeLabel}: ${args.contentTitle}`,
    args.hospitalName,
    regionLabel,
    args.directorName ? `${args.directorName} 원장 진료 분야` : '',
  ].filter(Boolean)
  const joined = parts.join(' — ')
  return joined.length > ALT_MAX_LENGTH ? `${joined.slice(0, ALT_MAX_LENGTH - 1)}…` : joined
}

function formatDate(value: string | null | undefined, fallback: string) {
  if (!value) return fallback
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return fallback
  return parsed.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
}

function calculateReadingMinutes(body: string | null | undefined): number {
  if (!body) return 1
  // 마크다운 기호·URL 노이즈 차감 후 글자수 추정.
  const stripped = body.replace(/[#*_\[\]\(\)`>!\-]/g, '').replace(/https?:\/\/\S+/g, '')
  return Math.max(1, Math.round(stripped.length / KOREAN_READING_SPEED_CHARS_PER_MIN))
}

interface HowToStep {
  name: string
  text: string
}

// HowTo schema용 단계 추출. content_engine TREATMENT 프롬프트가 "### 1단계 ...",
// "### 2단계 ..." 형식으로 작성하도록 유도하므로 H3을 step으로 매핑.
function extractHowToSteps(body: string | null | undefined): HowToStep[] {
  if (!body) return []
  const lines = body.split('\n')
  const steps: HowToStep[] = []
  let current: HowToStep | null = null
  for (const rawLine of lines) {
    const line = rawLine.trim()
    const match = line.match(/^###\s+(.+)/)
    if (match) {
      if (current) steps.push(current)
      current = { name: match[1].trim(), text: '' }
      continue
    }
    if (current && line && !line.startsWith('#')) {
      current.text = current.text ? `${current.text} ${line}` : line
    }
  }
  if (current) steps.push(current)
  return steps.filter((s) => s.text.length > 0)
}

export async function generateMetadata({ params: paramsPromise }: Props): Promise<Metadata> {
  const params = await paramsPromise
  try {
    const [hospital, content] = await Promise.all([
      fetchHospital(params.slug),
      fetchContent(params.slug, params.contentId),
    ])
    const imageUrl = resolveAssetUrl(content.image_url)
    const description =
      content.meta_description ?? `${hospital.name}의 ${TYPE_LABELS[content.content_type] ?? '의료'} 콘텐츠`
    return {
      title: `${content.title} | ${hospital.name}`,
      description,
      alternates: { canonical: `/${params.slug}/contents/${params.contentId}` },
      openGraph: {
        title: `${content.title} | ${hospital.name}`,
        description,
        url: `/${params.slug}/contents/${params.contentId}`,
        type: 'article',
        images: imageUrl ? [{ url: imageUrl }] : [],
      },
    }
  } catch {
    return { title: '의료 정보' }
  }
}

export default async function ContentDetailPage({ params: paramsPromise }: Props) {
  const params = await paramsPromise
  let hospital
  let content
  let allContents
  try {
    ;[hospital, content, allContents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContent(params.slug, params.contentId),
      fetchContents(params.slug, 60),
    ])
  } catch (e) {
    if (e instanceof HospitalNotFoundError || e instanceof ContentNotFoundError) notFound()
    throw e
  }

  const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
  const publishedLabel = formatDate(content.published_at, content.scheduled_date)
  const updatedLabel = content.body_updated_at
    ? formatDate(content.body_updated_at, '')
    : publishedLabel
  const readingMinutes = calculateReadingMinutes(content.body)

  const otherContents = allContents.filter((c) => c.id !== content.id)
  const sameTypeRelated = otherContents
    .filter((c) => c.content_type === content.content_type)
    .slice(0, 3)
  const paaQuestions = otherContents
    .filter((c) => c.content_type === 'FAQ' && !sameTypeRelated.some((s) => s.id === c.id))
    .slice(0, 3)
  const referenceList = Array.isArray(content.references) ? content.references : []
  const referenceCountLabel =
    referenceList.length > 0 ? `참고자료 ${referenceList.length}건` : '참고자료 확인 중'

  const pillarTreatment = inferPillarTreatment(hospital.treatments || [], content)
  const pillarSlug = pillarTreatment ? buildTreatmentSlug(pillarTreatment.name) : ''
  const pillarHref = pillarSlug ? `/${params.slug}/treatments/${pillarSlug}` : null
  const pillarUrl = pillarSlug ? `${SITE_URL}/${params.slug}/treatments/${pillarSlug}` : null

  const breadcrumbItems = [
    { label: '홈', href: `/${params.slug}` },
    { label: '의료 정보', href: `/${params.slug}/contents` },
    { label: typeLabel },
    { label: content.title },
  ]

  const articleUrl = `${SITE_URL}/${params.slug}/contents/${params.contentId}`
  const datePublished = content.published_at || content.scheduled_date
  const dateModified = content.body_updated_at || content.published_at || content.scheduled_date

  // 모든 article 공통 base. type별 추가 schema는 jsonLd 배열에 별도로 push.
  const physicianId = `${SITE_URL}/${params.slug}/doctor#physician`
  const clinicId = `${SITE_URL}/${params.slug}#clinic`
  const articleImageUrl = resolveAssetUrl(content.image_url)
  const articleJsonLd: Record<string, unknown> = {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: content.title,
    // meta_description이 없으면 null 대신 키 자체를 생략한다 (JSON.stringify가 undefined 키 제거).
    description: content.meta_description ?? undefined,
    author: {
      '@type': 'MedicalClinic',
      '@id': clinicId,
      name: hospital.name,
    },
    publisher: {
      '@type': 'MedicalClinic',
      '@id': clinicId,
      name: hospital.name,
    },
    isPartOf: pillarUrl
      ? {
          '@type': 'CollectionPage',
          '@id': pillarUrl,
          name: pillarTreatment?.name,
          url: pillarUrl,
        }
      : undefined,
    datePublished,
    dateModified,
    mainEntityOfPage: articleUrl,
    image: articleImageUrl ?? undefined,
    citation:
      referenceList.length > 0
        ? referenceList.map((ref) => ({ '@type': 'CreativeWork', name: ref.title, url: ref.url }))
        : undefined,
    // Speakable: 음성 어시스턴트가 발췌해 읽을 수 있는 구간.
    speakable: {
      '@type': 'SpeakableSpecification',
      cssSelector: ['.clinic-article-title', '.clinic-article-tldr p'],
    },
  }

  const imageAlt = content.image_url
    ? buildImageAlt({
        contentTitle: content.title,
        typeLabel,
        hospitalName: hospital.name,
        region: hospital.region,
        directorName: hospital.director_name,
      })
    : ''

  const jsonLd: Record<string, unknown>[] = [
    articleJsonLd,
    buildBreadcrumbJsonLd(breadcrumbItems, SITE_URL),
  ]

  if (articleImageUrl) {
    jsonLd.push({
      '@context': 'https://schema.org',
      '@type': 'ImageObject',
      contentUrl: articleImageUrl,
      url: articleImageUrl,
      name: content.title,
      caption: imageAlt,
      description: content.meta_description ?? imageAlt,
      creator: {
        '@type': 'MedicalClinic',
        '@id': clinicId,
        name: hospital.name,
      },
      representativeOfPage: true,
      datePublished,
      license: `${SITE_URL}/terms`,
    })
  }

  // ── FAQ → FAQPage (Question/Answer는 분리 필드 사용. 미존재 시 fallback) ───
  if (content.content_type === 'FAQ') {
    const question = content.faq_question || content.title
    const answer = content.faq_answer_summary || content.meta_description || ''
    if (answer) {
      jsonLd.push({
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        mainEntity: [
          {
            '@type': 'Question',
            name: question,
            acceptedAnswer: { '@type': 'Answer', text: answer },
          },
        ],
      })
    }
  }

  // ── TREATMENT → MedicalProcedure + (단계 추출 시) HowTo ──────────────────
  if (content.content_type === 'TREATMENT') {
    jsonLd.push({
      '@context': 'https://schema.org',
      '@type': 'MedicalProcedure',
      name: content.title,
      description: content.meta_description ?? undefined,
      url: articleUrl,
      performer: {
        '@type': 'Physician',
        '@id': physicianId,
        name: hospital.director_name,
      },
    })
    const steps = extractHowToSteps(content.body)
    if (steps.length >= 2) {
      jsonLd.push({
        '@context': 'https://schema.org',
        '@type': 'HowTo',
        name: content.title,
        description: content.meta_description ?? undefined,
        step: steps.map((step, idx) => ({
          '@type': 'HowToStep',
          position: idx + 1,
          name: step.name,
          text: step.text,
        })),
      })
    }
  }

  // ── DISEASE → MedicalCondition + MedicalWebPage ────────────────────────
  if (content.content_type === 'DISEASE') {
    jsonLd.push({
      '@context': 'https://schema.org',
      '@type': 'MedicalWebPage',
      url: articleUrl,
      about: {
        '@type': 'MedicalCondition',
        name: content.title,
        description: content.meta_description ?? undefined,
      },
      audience: { '@type': 'MedicalAudience', audienceType: 'Patient' },
      dateModified,
    })
  }

  return (
    <>
      <JsonLd data={jsonLd} />
      <div className="clinic-shell">
        <ClinicHeader
          hospitalName={hospital.name}
          hospitalSlug={params.slug}
          region={hospital.region}
          specialties={hospital.specialties}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
        <main id="main-content">
          <div className="clinic-article-shell">
            <article className="clinic-article">
              {articleImageUrl && (
                <figure className="clinic-article-cover-figure">
                  <div className="clinic-article-cover">
                    <Image
                      src={articleImageUrl}
                      alt={imageAlt}
                      fill
                      sizes="(max-width: 960px) 100vw, 720px"
                      style={{ objectFit: 'cover' }}
                      priority
                      unoptimized={shouldBypassNextImageOptimization(articleImageUrl)}
                    />
                  </div>
                </figure>
              )}
              <div className="clinic-article-header">
                <Breadcrumb items={breadcrumbItems} />
                <span className="clinic-article-type">{typeLabel}</span>
                {pillarHref && pillarTreatment && (
                  <p className="clinic-article-pillar-link">
                    <span className="clinic-article-pillar-label">속한 진료 영역</span>
                    <Link href={pillarHref}>{pillarTreatment.name} 안내 모음 보기 →</Link>
                  </p>
                )}
                <h1 className="clinic-article-title">{content.title}</h1>
                <p className="clinic-article-byline">
                  <span className="clinic-article-byline-label">의료 정보 안내</span>
                  <strong>{hospital.name}</strong>
                  <span className="clinic-article-byline-dot" aria-hidden="true">·</span>
                  <span>{hospital.director_name} 원장 진료 분야 기준</span>
                  <span className="clinic-article-byline-dot" aria-hidden="true">·</span>
                  <span>{readingMinutes}분 읽기</span>
                  <span className="clinic-article-byline-dot" aria-hidden="true">·</span>
                  <span>
                    발행{' '}
                    <time dateTime={datePublished}>{publishedLabel}</time>
                  </span>
                  {updatedLabel && updatedLabel !== publishedLabel && (
                    <>
                      <span className="clinic-article-byline-dot" aria-hidden="true">·</span>
                      <span>
                        최근 업데이트{' '}
                        <time dateTime={dateModified}>{updatedLabel}</time>
                      </span>
                    </>
                  )}
                  <span className="clinic-article-byline-chip">개인별 판단은 진료 상담 필요</span>
                </p>
                <dl className="clinic-article-trustbar" aria-label="콘텐츠 신뢰 정보">
                  <div>
                    <dt>작성 기준</dt>
                    <dd>{hospital.director_name} 원장 진료 분야</dd>
                  </div>
                  <div>
                    <dt>업데이트</dt>
                    <dd>
                      <time dateTime={dateModified}>{updatedLabel || publishedLabel}</time>
                    </dd>
                  </div>
                  <div>
                    <dt>출처</dt>
                    <dd>{referenceCountLabel}</dd>
                  </div>
                  <div>
                    <dt>고지</dt>
                    <dd>진료 상담 대체 아님</dd>
                  </div>
                </dl>
                {articleImageUrl && (
                  <p className="clinic-article-media-note">
                    대표 이미지는 이해를 돕기 위한 의료 일러스트이며 실제 진단 결과나 환자 사례가 아닙니다.
                  </p>
                )}
              </div>

              {content.meta_description && (
                <aside className="clinic-article-tldr" aria-label="핵심 답변 요약">
                  <span className="clinic-article-tldr-eyebrow">핵심 답변</span>
                  <p>{content.meta_description}</p>
                </aside>
              )}

              {content.content_type === 'FAQ' && content.faq_question && (
                <dl className="clinic-article-faq" aria-label="자주 묻는 질문">
                  <dt>{content.faq_question}</dt>
                  {content.faq_answer_summary && <dd>{content.faq_answer_summary}</dd>}
                </dl>
              )}

              <div className="clinic-article-body">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    table: ({ children, node, ...props }) => {
                      void node
                      return (
                        <div className="clinic-markdown-table" role="region" aria-label="표">
                          <table {...props}>{children}</table>
                        </div>
                      )
                    },
                  }}
                >
                  {content.body}
                </ReactMarkdown>
              </div>

              {referenceList.length > 0 && (
                <section className="clinic-article-references" aria-label="참고 자료">
                  <h2 className="clinic-article-references-title">참고 자료</h2>
                  <ol>
                    {referenceList.map((ref, idx) => {
                      const sourceLabel = ref.source_type
                        ? SOURCE_TYPE_LABELS[ref.source_type]
                        : null
                      const safeHref = safeExternalHref(ref.url)
                      return (
                        <li key={`${ref.url}-${idx}`}>
                          {safeHref ? (
                            <a href={safeHref} target="_blank" rel="noopener noreferrer nofollow">
                              {sourceLabel && (
                                <span className="clinic-article-references-source" aria-label="출처 분류">
                                  {sourceLabel}
                                </span>
                              )}
                              {ref.title}
                              <ExternalIcon
                                className="clinic-icon clinic-icon--sm"
                                style={{ color: 'currentColor' }}
                              />
                            </a>
                          ) : (
                            <span>
                              {sourceLabel && (
                                <span className="clinic-article-references-source" aria-label="출처 분류">
                                  {sourceLabel}
                                </span>
                              )}
                              {ref.title}
                            </span>
                          )}
                        </li>
                      )
                    })}
                  </ol>
                  <p className="clinic-article-references-note">
                    위 자료는 본 콘텐츠 작성 시 인용한 공개 자료입니다. 진료 결정은 의료진 상담이 우선합니다.
                  </p>
                </section>
              )}
            </article>

            <aside className="clinic-aside" aria-label="병원 정보 및 관련 콘텐츠">
              <div className="clinic-aside-card">
                <span className="clinic-aside-card-eyebrow">병원 정보</span>
                <h2 className="clinic-aside-card-title">{hospital.name}</h2>
                <address className="clinic-aside-meta clinic-aside-address">
                  <span>
                    <span className="clinic-aside-meta-label">주소</span>
                    <span>{hospital.address}</span>
                  </span>
                  <span>
                    <span className="clinic-aside-meta-label">전화</span>
                    <a
                      href={`tel:${hospital.phone}`}
                      style={{ color: 'var(--color-revisit-primary-40)', fontWeight: 600 }}
                    >
                      {hospital.phone}
                    </a>
                  </span>
                </address>
                <Link
                  href={`/${params.slug}`}
                  className="clinic-btn clinic-btn-secondary"
                  style={{ width: '100%', justifyContent: 'center', height: 40, fontSize: 14, marginBottom: 8 }}
                >
                  병원 의료 정보 홈으로
                </Link>
                {hospital.website_url && (
                  <a
                    href={hospital.website_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="clinic-btn clinic-btn-primary"
                    style={{ width: '100%', justifyContent: 'center', height: 40, fontSize: 14 }}
                  >
                    공식 홈페이지로 이동
                  </a>
                )}
              </div>

              {paaQuestions.length > 0 && (
                <div className="clinic-aside-card clinic-aside-card--paa">
                  <span className="clinic-aside-card-eyebrow">함께 보는 질문</span>
                  <h2 className="clinic-aside-card-title">환자가 함께 묻는 질문</h2>
                  <ul className="clinic-paa-list">
                    {paaQuestions.map((q) => (
                      <li key={q.id}>
                        <Link href={`/${params.slug}/contents/${q.id}`} className="clinic-paa-link">
                          <span className="clinic-paa-q">Q.</span>
                          <span>{q.title}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {sameTypeRelated.length > 0 && (
                <div className="clinic-aside-card">
                  <span className="clinic-aside-card-eyebrow">관련 글</span>
                  <h2 className="clinic-aside-card-title">관련 {typeLabel}</h2>
                  <ul className="clinic-related-list">
                    {sameTypeRelated.map((r) => {
                      const relatedImageUrl = resolveAssetUrl(r.image_url)
                      return (
                        <li key={r.id}>
                          <Link href={`/${params.slug}/contents/${r.id}`} className="clinic-related-item">
                            {relatedImageUrl ? (
                              <span className="clinic-related-thumb">
                                <Image
                                  src={relatedImageUrl}
                                  alt={r.title}
                                  fill
                                  sizes="56px"
                                  style={{ objectFit: 'cover' }}
                                  unoptimized={shouldBypassNextImageOptimization(relatedImageUrl)}
                                />
                              </span>
                            ) : (
                              <span className="clinic-related-thumb" aria-hidden="true" />
                            )}
                            <span className="clinic-related-meta">
                              <span className="clinic-related-title">{r.title}</span>
                              <span className="clinic-related-date">
                                {formatDate(r.published_at, r.scheduled_date)}
                              </span>
                            </span>
                          </Link>
                        </li>
                      )
                    })}
                  </ul>
                </div>
              )}
            </aside>
          </div>
        </main>
        <ClinicFooter
          hospitalName={hospital.name}
          address={hospital.address}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
      </div>
    </>
  )
}
