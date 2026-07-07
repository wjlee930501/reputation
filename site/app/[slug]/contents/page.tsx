import { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'

import { fetchContents, fetchHospital, HospitalNotFoundError, TYPE_LABELS, type ContentSummary } from '@/lib/api'
import { categoryTagClass } from '@/lib/content-meta'
import { buildFaqPageJsonLd } from '@/lib/schema'
import { canonicalBase } from '@/lib/site-url'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { ChevronRightIcon } from '../_components/icons'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicHeader } from '../_components/ClinicHeader'
import { JsonLd } from '../_components/JsonLd'

interface Props {
  params: Promise<{ slug: string }>
  searchParams: Promise<{ type?: string }>
}

export const revalidate = 3600

const PRIORITY_TYPES = ['FAQ', 'DISEASE', 'TREATMENT', 'COLUMN', 'HEALTH', 'LOCAL', 'NOTICE']

function contentDate(content: ContentSummary): number {
  const value = content.published_at || content.scheduled_date
  const parsed = value ? new Date(value).getTime() : NaN
  return Number.isNaN(parsed) ? 0 : parsed
}

function formatDate(value: string | null | undefined, fallback: string) {
  if (!value) return fallback
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return fallback
  return parsed.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
}

export async function generateMetadata({ params: paramsPromise }: Props): Promise<Metadata> {
  const params = await paramsPromise
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.name} 의료 정보 — 자주 묻는 질문, 질환 정보, 치료 안내, 원장 칼럼.`
    const canonicalUrl = `${canonicalBase(hospital)}/${params.slug}/contents`
    return {
      title: `${hospital.name} 의료 정보`,
      description,
      alternates: { canonical: canonicalUrl },
      openGraph: {
        title: `${hospital.name} 의료 정보`,
        description,
        url: canonicalUrl,
        type: 'website',
      },
    }
  } catch {
    return { title: '의료 정보' }
  }
}

export default async function ContentsLibraryPage({ params: paramsPromise, searchParams: searchParamsPromise }: Props) {
  const [params, search] = await Promise.all([paramsPromise, searchParamsPromise])
  let hospital
  let contents
  try {
    ;[hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 500),
    ])
  } catch (e) {
    if (e instanceof HospitalNotFoundError) notFound()
    throw e
  }

  // 유형별 개수 집계 + 표시 순서(우선순위 유형 → 나머지).
  const counts = new Map<string, number>()
  for (const content of contents) {
    counts.set(content.content_type, (counts.get(content.content_type) ?? 0) + 1)
  }
  const availableTypes = [
    ...PRIORITY_TYPES.filter((type) => counts.has(type)),
    ...Array.from(counts.keys()).filter((type) => !PRIORITY_TYPES.includes(type)),
  ]

  // 필터는 클라이언트 상태 없이 쿼리 파라미터 링크로만 구현 — 서버 컴포넌트 유지.
  const activeType = search.type && counts.has(search.type) ? search.type : null

  // 통합 시간순(최신 먼저) 피드. 필터가 있으면 해당 유형만.
  const sorted = [...contents].sort((a, b) => contentDate(b) - contentDate(a))
  const filtered = activeType ? sorted.filter((c) => c.content_type === activeType) : sorted
  const [featured, ...feedRest] = filtered

  const breadcrumbItems = [
    { label: '홈', href: `/${params.slug}` },
    { label: '의료 정보' },
  ]

  const base = canonicalBase(hospital)

  const collectionJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'CollectionPage',
    name: `${hospital.name} 의료 정보`,
    about: hospital.specialties,
    isPartOf: {
      '@type': 'WebSite',
      name: hospital.name,
      url: `${base}/${params.slug}`,
    },
    hasPart: contents.map((content) => ({
      '@type': 'Article',
      headline: content.title,
      url: `${base}/${params.slug}/contents/${content.id}`,
      datePublished: content.published_at || content.scheduled_date,
    })),
  }

  const faqJsonLd = buildFaqPageJsonLd(contents, base, params.slug)
  const pageJsonLd = [
    collectionJsonLd,
    buildBreadcrumbJsonLd(breadcrumbItems, base),
    ...(faqJsonLd ? [faqJsonLd] : []),
  ]

  const chipHref = (type: string | null) =>
    type ? `/${params.slug}/contents?type=${type}` : `/${params.slug}/contents`

  return (
    <>
      <JsonLd data={pageJsonLd} />
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
          <section className="clinic-library-hero">
            <div className="clinic-library-hero-inner">
              <Breadcrumb items={breadcrumbItems} />
              <h1 className="clinic-library-hero-title">{hospital.name} 의료 정보</h1>
              <p className="clinic-library-hero-meta">
                <span>{hospital.specialties.join(' · ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.region.join(' ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <strong>{hospital.director_name} 원장</strong>
              </p>
              <p className="clinic-library-hero-note">
                진료실에서 자주 나오는 질문과 치료 전 확인하면 좋은 내용을 모았습니다.
                개인의 상태에 따라 판단이 달라질 수 있으니 자세한 내용은 진료 상담에서 확인해 주세요.
              </p>
            </div>
          </section>

          <section className="clinic-section clinic-section--tight">
            <div className="clinic-section-inner">
              {contents.length === 0 ? (
                <div className="clinic-empty">
                  <span className="clinic-empty-title">아직 발행된 콘텐츠가 없습니다</span>
                  <p>진료 안내와 건강 정보 글을 준비하고 있습니다.</p>
                </div>
              ) : (
                <>
                  <nav className="clinic-filter-chips" aria-label="유형별 필터">
                    <Link
                      href={chipHref(null)}
                      className="clinic-filter-chip"
                      aria-current={activeType === null ? 'page' : undefined}
                    >
                      전체 <span className="clinic-filter-chip-count">{contents.length}</span>
                    </Link>
                    {availableTypes.map((type) => (
                      <Link
                        key={type}
                        href={chipHref(type)}
                        className="clinic-filter-chip"
                        aria-current={activeType === type ? 'page' : undefined}
                      >
                        {TYPE_LABELS[type] ?? type}{' '}
                        <span className="clinic-filter-chip-count">{counts.get(type)}</span>
                      </Link>
                    ))}
                  </nav>

                  {featured && (
                    <Link
                      href={`/${params.slug}/contents/${featured.id}`}
                      className="clinic-feed-featured"
                      aria-label={`대표 글 — ${featured.title}`}
                    >
                      <span className="clinic-feed-featured-kicker">
                        {activeType ? `${TYPE_LABELS[activeType] ?? activeType} · 최신 글` : '가장 최근 글'}
                      </span>
                      <span className={`clinic-tag ${categoryTagClass(featured.content_type)}`}>
                        {TYPE_LABELS[featured.content_type] ?? featured.content_type}
                      </span>
                      <h2 className="clinic-feed-featured-title">{featured.faq_question || featured.title}</h2>
                      {featured.meta_description && (
                        <p className="clinic-feed-featured-excerpt">{featured.meta_description}</p>
                      )}
                      <span className="clinic-feed-featured-meta">
                        <strong>{hospital.director_name} 원장</strong>
                        <span className="clinic-content-card-meta-dot" aria-hidden="true" />
                        <span>{formatDate(featured.published_at, featured.scheduled_date)}</span>
                        <span className="clinic-content-card-meta-dot" aria-hidden="true" />
                        <span>{featured.reading_minutes ?? 1}분 분량</span>
                      </span>
                    </Link>
                  )}

                  {feedRest.length > 0 && (
                    <ol className="clinic-feed-list">
                      {feedRest.map((content) => {
                        const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
                        return (
                          <li key={content.id}>
                            <Link
                              href={`/${params.slug}/contents/${content.id}`}
                              className="clinic-feed-row"
                              aria-label={`${typeLabel} — ${content.title}`}
                            >
                              <span className="clinic-feed-row-main">
                                <span className={`clinic-tag clinic-tag--sm ${categoryTagClass(content.content_type)}`}>
                                  {typeLabel}
                                </span>
                                <span className="clinic-feed-row-title">
                                  {content.faq_question || content.title}
                                </span>
                                {content.meta_description && (
                                  <span className="clinic-feed-row-excerpt">{content.meta_description}</span>
                                )}
                              </span>
                              <span className="clinic-feed-row-aside">
                                <span className="clinic-feed-row-date">
                                  {formatDate(content.published_at, content.scheduled_date)}
                                </span>
                                <ChevronRightIcon className="clinic-icon clinic-feed-row-arrow" aria-hidden="true" />
                              </span>
                            </Link>
                          </li>
                        )
                      })}
                    </ol>
                  )}
                </>
              )}
            </div>
          </section>
        </main>
        <ClinicFooter
          hospitalName={hospital.name}
          directorName={hospital.director_name}
          address={hospital.address}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
      </div>
    </>
  )
}
