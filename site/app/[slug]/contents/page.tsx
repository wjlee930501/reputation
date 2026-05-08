import { Metadata } from 'next'
import { notFound } from 'next/navigation'

import { fetchContents, fetchHospital, TYPE_LABELS, type ContentItem } from '@/lib/api'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicHeader } from '../_components/ClinicHeader'
import { ContentCard } from '../_components/ContentCard'
import { JsonLd } from '../_components/JsonLd'

interface Props {
  params: { slug: string }
}

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'

const PRIORITY_TYPES = ['FAQ', 'DISEASE', 'TREATMENT', 'COLUMN', 'HEALTH', 'LOCAL', 'NOTICE']

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.name} 의료 콘텐츠 허브 라이브러리 — 자주 묻는 질문, 질환 가이드, 시술 안내, 원장 칼럼.`
    return {
      title: `${hospital.name} 의료 콘텐츠 라이브러리`,
      description,
      alternates: { canonical: `/${params.slug}/contents` },
      openGraph: {
        title: `${hospital.name} 의료 콘텐츠 라이브러리`,
        description,
        url: `/${params.slug}/contents`,
        type: 'website',
      },
    }
  } catch {
    return { title: '의료 콘텐츠 라이브러리' }
  }
}

export default async function ContentsLibraryPage({ params }: Props) {
  let hospital
  let contents
  try {
    ;[hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 500),
    ])
  } catch {
    notFound()
  }

  const grouped = new Map<string, ContentItem[]>()
  for (const content of contents) {
    const list = grouped.get(content.content_type) ?? []
    list.push(content)
    grouped.set(content.content_type, list)
  }
  const orderedTypes = [
    ...PRIORITY_TYPES.filter((type) => grouped.has(type)),
    ...[...grouped.keys()].filter((type) => !PRIORITY_TYPES.includes(type)),
  ]

  const breadcrumbItems = [
    { label: '홈', href: `/${params.slug}` },
    { label: '의료 콘텐츠 라이브러리' },
  ]

  const collectionJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'CollectionPage',
    name: `${hospital.name} 의료 콘텐츠 라이브러리`,
    about: hospital.specialties,
    isPartOf: {
      '@type': 'WebSite',
      name: hospital.name,
      url: `${SITE_URL}/${params.slug}`,
    },
    hasPart: contents.map((content) => ({
      '@type': 'Article',
      headline: content.title,
      url: `${SITE_URL}/${params.slug}/contents/${content.id}`,
      datePublished: content.published_at || content.scheduled_date,
    })),
  }

  return (
    <>
      <JsonLd data={[collectionJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, SITE_URL)]} />
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
          <section className="clinic-library-hero">
            <div className="clinic-library-hero-inner">
              <Breadcrumb items={breadcrumbItems} />
              <span className="clinic-section-eyebrow">Medical Content Library</span>
              <h1 className="clinic-library-hero-title">{hospital.name} 의료 콘텐츠 라이브러리</h1>
              <p className="clinic-library-hero-meta">
                <strong>{contents.length}편</strong>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.specialties.join(' · ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.region.join(' ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <strong>{hospital.director_name} 원장 큐레이션</strong>
              </p>
              <p
                className="clinic-section-lede"
                style={{ marginTop: 16, maxWidth: 720, fontSize: 14 }}
              >
                환자가 자주 묻는 질문에 답하는 의료 콘텐츠를 유형별로 모았습니다. 모든 글은 발행 전
                의료광고 표현 검토를 거치며, AI 답변 서비스(ChatGPT·Gemini)가 인용할 수 있도록 구조화 데이터로 제공됩니다.
              </p>
            </div>
          </section>

          <section className="clinic-section">
            <div className="clinic-section-inner">
              {contents.length === 0 ? (
                <div className="clinic-empty">
                  <span className="clinic-empty-title">아직 발행된 콘텐츠가 없습니다</span>
                  <p>운영 일정에 따라 정기적으로 의료 콘텐츠가 추가됩니다.</p>
                </div>
              ) : (
                <div>
                  {orderedTypes.map((type) => {
                    const items = grouped.get(type) ?? []
                    return (
                      <section key={type} className="clinic-content-group">
                        <header className="clinic-content-group-header">
                          <h2 className="clinic-content-group-heading">
                            {TYPE_LABELS[type] ?? type}
                          </h2>
                          <span className="clinic-content-group-count">{items.length}편</span>
                        </header>
                        <div className="clinic-content-grid">
                          {items.map((content) => (
                            <ContentCard
                              key={content.id}
                              content={content}
                              hospitalSlug={params.slug}
                              hospitalName={hospital.name}
                            />
                          ))}
                        </div>
                      </section>
                    )
                  })}
                </div>
              )}
            </div>
          </section>
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
