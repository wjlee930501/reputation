import { Metadata } from 'next'
import Image from 'next/image'
import Link from 'next/link'
import { notFound } from 'next/navigation'
import ReactMarkdown from 'react-markdown'

import { fetchContent, fetchContents, fetchHospital, TYPE_LABELS } from '@/lib/api'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../../_components/Breadcrumb'
import { ClinicFooter } from '../../_components/ClinicFooter'
import { ClinicHeader } from '../../_components/ClinicHeader'
import { JsonLd } from '../../_components/JsonLd'

interface Props {
  params: { slug: string; contentId: string }
}

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'

function formatDate(value: string | null | undefined, fallback: string) {
  if (!value) return fallback
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return fallback
  return parsed.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
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
  const dateLabel = formatDate(content.published_at, content.scheduled_date)
  const related = allContents
    .filter((c) => c.id !== content.id && c.content_type === content.content_type)
    .slice(0, 3)

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
    dateModified: content.published_at || content.scheduled_date,
    mainEntityOfPage: `${SITE_URL}/${params.slug}/contents/${params.contentId}`,
    image: content.image_url ?? undefined,
  }

  // FAQ 콘텐츠는 FAQPage 구조화 데이터를 함께 노출 — ChatGPT/Gemini 인용 신호.
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
                text: content.body,
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
                  <span style={{ color: 'var(--color-revisit-text-caption)', fontWeight: 600 }}>큐레이터</span>
                  <strong>{hospital.director_name} 원장</strong>
                  <span aria-hidden="true">·</span>
                  <span>{dateLabel}</span>
                  <span aria-hidden="true">·</span>
                  <span style={{ color: 'var(--color-revisit-green-30)', fontWeight: 600 }}>발행 시점 검수 완료</span>
                </p>
              </div>
              <div className="clinic-article-body">
                <ReactMarkdown>{content.body}</ReactMarkdown>
              </div>
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

              {related.length > 0 && (
                <div className="clinic-aside-card">
                  <span className="clinic-aside-card-eyebrow">Related</span>
                  <h2 className="clinic-aside-card-title">관련 {typeLabel}</h2>
                  <ul className="clinic-related-list">
                    {related.map((r) => (
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
