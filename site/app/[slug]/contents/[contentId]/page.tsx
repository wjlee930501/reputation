import { Metadata } from 'next'
import Image from 'next/image'
import Link from 'next/link'
import { notFound } from 'next/navigation'
import ReactMarkdown from 'react-markdown'

import { fetchContent, fetchContents, fetchHospital, TYPE_LABELS } from '@/lib/api'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../../_components/Breadcrumb'
import { ClinicFooter } from '../../_components/ClinicFooter'
import { ClinicHeader } from '../../_components/ClinicHeader'
import { ExternalIcon } from '../../_components/icons'
import { JsonLd } from '../../_components/JsonLd'

interface Props {
  params: { slug: string; contentId: string }
}

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'

// 한국어 평균 읽기 속도 약 600자/분.
const KOREAN_READING_SPEED_CHARS_PER_MIN = 600

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

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  try {
    const [hospital, content] = await Promise.all([
      fetchHospital(params.slug),
      fetchContent(params.slug, params.contentId),
    ])
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
        images: content.image_url ? [{ url: content.image_url }] : [],
      },
    }
  } catch {
    return { title: '의료 정보' }
  }
}

export default async function ContentDetailPage({ params }: Props) {
  let hospital
  let content
  let allContents
  try {
    ;[hospital, content, allContents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContent(params.slug, params.contentId),
      fetchContents(params.slug, 60),
    ])
  } catch {
    notFound()
  }

  const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
  const publishedLabel = formatDate(content.published_at, content.scheduled_date)
  const reviewedLabel = content.body_updated_at
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

  const breadcrumbItems = [
    { label: '홈', href: `/${params.slug}` },
    { label: '의료 정보', href: `/${params.slug}/contents` },
    { label: typeLabel },
    { label: content.title },
  ]

  const articleJsonLd: Record<string, unknown> = {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: content.title,
    description: content.meta_description,
    author: {
      '@type': 'Physician',
      name: hospital.director_name,
    },
    publisher: {
      '@type': 'MedicalClinic',
      name: hospital.name,
    },
    datePublished: content.published_at || content.scheduled_date,
    dateModified: content.body_updated_at || content.published_at || content.scheduled_date,
    mainEntityOfPage: `${SITE_URL}/${params.slug}/contents/${params.contentId}`,
    image: content.image_url ?? undefined,
    citation: referenceList.length > 0
      ? referenceList.map((ref) => ({
          '@type': 'CreativeWork',
          name: ref.title,
          url: ref.url,
        }))
      : undefined,
  }

  // FAQ 콘텐츠는 FAQPage schema 노출. AI 답변 인용 신호.
  const faqJsonLd =
    content.content_type === 'FAQ'
      ? {
          '@context': 'https://schema.org',
          '@type': 'FAQPage',
          mainEntity: [
            {
              '@type': 'Question',
              name: content.title,
              acceptedAnswer: {
                '@type': 'Answer',
                text: content.meta_description ?? content.body,
              },
            },
          ],
        }
      : null

  const jsonLd = [articleJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, SITE_URL)]
  if (faqJsonLd) jsonLd.push(faqJsonLd)

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
        <main>
          <div className="clinic-article-shell">
            <article className="clinic-article">
              {content.image_url && (
                <div className="clinic-article-cover">
                  <Image
                    src={content.image_url}
                    alt={content.title}
                    fill
                    sizes="(max-width: 960px) 100vw, 720px"
                    style={{ objectFit: 'cover' }}
                    priority
                  />
                </div>
              )}
              <div className="clinic-article-header">
                <Breadcrumb items={breadcrumbItems} />
                <span className="clinic-article-type">{typeLabel}</span>
                <h1 className="clinic-article-title">{content.title}</h1>
                <p className="clinic-article-byline">
                  <span className="clinic-article-byline-label">큐레이터</span>
                  <strong>{hospital.director_name} 원장</strong>
                  <span className="clinic-article-byline-dot" aria-hidden="true">·</span>
                  <span>{readingMinutes}분 읽기</span>
                  <span className="clinic-article-byline-dot" aria-hidden="true">·</span>
                  <span>발행 {publishedLabel}</span>
                  {reviewedLabel && reviewedLabel !== publishedLabel && (
                    <>
                      <span className="clinic-article-byline-dot" aria-hidden="true">·</span>
                      <span>최근 검수 {reviewedLabel}</span>
                    </>
                  )}
                  <span className="clinic-article-byline-chip">발행 시점 검수 완료</span>
                </p>
              </div>

              {content.meta_description && (
                <aside className="clinic-article-tldr" aria-label="핵심 답변 요약">
                  <span className="clinic-article-tldr-eyebrow">핵심 답변</span>
                  <p>{content.meta_description}</p>
                </aside>
              )}

              <div className="clinic-article-body">
                <ReactMarkdown>{content.body}</ReactMarkdown>
              </div>

              {referenceList.length > 0 && (
                <section className="clinic-article-references" aria-label="참고 자료">
                  <h2 className="clinic-article-references-title">참고 자료</h2>
                  <ol>
                    {referenceList.map((ref, idx) => (
                      <li key={`${ref.url}-${idx}`}>
                        <a href={ref.url} target="_blank" rel="noopener nofollow">
                          {ref.title}
                          <ExternalIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
                        </a>
                      </li>
                    ))}
                  </ol>
                  <p className="clinic-article-references-note">
                    위 자료는 본 콘텐츠 작성 시 인용한 공개 자료입니다. 진료 결정은 의료진 상담이 우선합니다.
                  </p>
                </section>
              )}
            </article>

            <aside className="clinic-aside" aria-label="병원 정보 및 관련 콘텐츠">
              <div className="clinic-aside-card">
                <span className="clinic-aside-card-eyebrow">Clinic</span>
                <h2 className="clinic-aside-card-title">{hospital.name}</h2>
                <ul className="clinic-aside-meta">
                  <li>
                    <span className="clinic-aside-meta-label">주소</span>
                    <span>{hospital.address}</span>
                  </li>
                  <li>
                    <span className="clinic-aside-meta-label">전화</span>
                    <a
                      href={`tel:${hospital.phone}`}
                      style={{ color: 'var(--color-revisit-primary-40)', fontWeight: 600 }}
                    >
                      {hospital.phone}
                    </a>
                  </li>
                </ul>
                <Link
                  href={`/${params.slug}`}
                  className="clinic-btn clinic-btn-secondary"
                  style={{ width: '100%', justifyContent: 'center', height: 40, fontSize: 14, marginBottom: 8 }}
                >
                  콘텐츠 허브 홈으로
                </Link>
                {hospital.website_url && (
                  <a
                    href={hospital.website_url}
                    target="_blank"
                    rel="noopener"
                    className="clinic-btn clinic-btn-primary"
                    style={{ width: '100%', justifyContent: 'center', height: 40, fontSize: 14 }}
                  >
                    공식 홈페이지로 이동
                  </a>
                )}
              </div>

              {paaQuestions.length > 0 && (
                <div className="clinic-aside-card clinic-aside-card--paa">
                  <span className="clinic-aside-card-eyebrow">People Also Ask</span>
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
                  <span className="clinic-aside-card-eyebrow">Related</span>
                  <h2 className="clinic-aside-card-title">관련 {typeLabel}</h2>
                  <ul className="clinic-related-list">
                    {sameTypeRelated.map((r) => (
                      <li key={r.id}>
                        <Link href={`/${params.slug}/contents/${r.id}`} className="clinic-related-item">
                          {r.image_url ? (
                            <span className="clinic-related-thumb">
                              <Image
                                src={r.image_url}
                                alt={r.title}
                                fill
                                sizes="56px"
                                style={{ objectFit: 'cover' }}
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
                    ))}
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
