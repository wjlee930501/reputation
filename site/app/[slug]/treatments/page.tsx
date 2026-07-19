import { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'

import { fetchContents, fetchHospital, HospitalNotFoundError, type ContentSummary } from '@/lib/api'
import { buildClinicThemeStyle } from '@/lib/clinic-theme'
import { canonicalBase } from '@/lib/site-url'

import { buildTreatmentSlug, inferPillarTreatment } from '@/lib/treatment-slug'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { ChevronRightIcon } from '../_components/icons'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicHeader } from '../_components/ClinicHeader'
import { ContentCard } from '../_components/ContentCard'
import { JsonLd } from '../_components/JsonLd'

interface Props {
  params: Promise<{ slug: string }>
}

export const revalidate = 3600

function findRelatedContents(
  treatmentName: string,
  treatments: Array<{ name: string; description: string }>,
  contents: ContentSummary[],
): ContentSummary[] {
  const stem = treatmentName.replace(/[수술치료시술검사진료]/g, '').trim()
  const lowerStem = stem.toLowerCase()
  return contents
    .filter((c) => {
      const linked = inferPillarTreatment(treatments, c)
      if (linked) return linked.name === treatmentName
      if (!lowerStem) return false
      const haystack = `${c.title} ${c.meta_description ?? ''} ${c.faq_question ?? ''}`.toLowerCase()
      return haystack.includes(lowerStem)
    })
    .slice(0, 3)
}

export async function generateMetadata({ params: paramsPromise }: Props): Promise<Metadata> {
  const params = await paramsPromise
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.name}의 진료 영역 — ${(hospital.treatments || []).map((t) => t.name).join(', ')}. 환자가 확인하면 좋은 진료 안내를 함께 제공합니다.`
    const canonicalUrl = `${canonicalBase(hospital)}/${params.slug}/treatments`
    return {
      title: `진료 영역 | ${hospital.name}`,
      description,
      alternates: { canonical: canonicalUrl },
      openGraph: {
        title: `진료 영역 | ${hospital.name}`,
        description,
        url: canonicalUrl,
        type: 'website',
      },
    }
  } catch {
    return { title: '진료 영역' }
  }
}

export default async function TreatmentsPage({ params: paramsPromise }: Props) {
  const params = await paramsPromise
  let hospital
  let contents
  try {
    ;[hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 200),
    ])
  } catch (e) {
    if (e instanceof HospitalNotFoundError) notFound()
    throw e
  }

  const treatments = hospital.treatments || []
  const breadcrumbItems = [
    { label: '홈', href: `/${params.slug}` },
    { label: '진료 영역' },
  ]

  const treatmentsWithRelated = treatments.map((t) => ({
    treatment: t,
    related: findRelatedContents(t.name, treatments, contents),
  }))

  const itemListJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    name: `${hospital.name} 진료 영역`,
    itemListElement: treatments.map((t, idx) => ({
      '@type': 'ListItem',
      position: idx + 1,
      item: {
        '@type': 'MedicalProcedure',
        name: t.name,
        performer: { '@type': 'MedicalClinic', name: hospital.name },
      },
    })),
  }

  return (
    <>
      <JsonLd data={[itemListJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, canonicalBase(hospital))]} />
      <div className="clinic-shell clinic-shell--editorial" style={buildClinicThemeStyle(hospital)}>
        <ClinicHeader
          hospitalName={hospital.name}
          hospitalSlug={params.slug}
          region={hospital.region}
          specialties={hospital.specialties}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
          logoUrl={hospital.logo_url}
        />
        <main id="main-content">
          <section className="clinic-library-hero">
            <div className="clinic-library-hero-inner">
              <Breadcrumb items={breadcrumbItems} />
              <h1 className="clinic-library-hero-title">{hospital.name} 진료 영역</h1>
              <p className="clinic-library-hero-meta">
                <strong>{treatments.length}개 진료 영역</strong>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.specialties.join(' · ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.region.join(' ')}</span>
              </p>
              <p
                className="clinic-section-lede"
                style={{ marginTop: 16, maxWidth: 720, fontSize: 14 }}
              >
                각 진료 영역에서 다루는 항목과 환자가 자주 묻는 내용을 함께 정리했습니다.
                정확한 진단과 치료 계획은 진료 상담을 통해 확인해 주세요.
              </p>
            </div>
          </section>

          {treatmentsWithRelated.length === 0 ? (
            <section className="clinic-section">
              <div className="clinic-section-inner">
                <div className="clinic-empty">
                  <span className="clinic-empty-title">아직 등록된 진료 영역이 없습니다</span>
                  <p>진료 영역 정보를 준비하고 있습니다.</p>
                </div>
              </div>
            </section>
          ) : (
            <section className="clinic-section clinic-section--tight">
              <div className="clinic-section-inner">
                <ul className="clinic-tx-deflist" aria-label="진료 영역 목록">
                  {treatmentsWithRelated.map(({ treatment }, idx) => {
                    const treatmentSlug = buildTreatmentSlug(treatment.name)
                    const href = treatmentSlug
                      ? `/${params.slug}/treatments/${treatmentSlug}`
                      : null
                    const className = `clinic-tx-row${idx === 0 ? ' clinic-tx-row--lead' : ''}`
                    const inner = (
                      <>
                        <span className="clinic-tx-term">{treatment.name}</span>
                        <span className="clinic-tx-desc">
                          {treatment.description || '진료 상담에서 자세한 내용을 확인해 주세요.'}
                        </span>
                        {href && (
                          <ChevronRightIcon
                            className="clinic-icon clinic-icon--sm clinic-tx-arrow"
                            aria-hidden="true"
                          />
                        )}
                      </>
                    )
                    return (
                      <li key={treatment.name} className="clinic-tx-item">
                        {href ? (
                          <Link href={href} className={className}>
                            {inner}
                          </Link>
                        ) : (
                          <div className={className}>{inner}</div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </div>
            </section>
          )}

          {treatmentsWithRelated.some((t) => t.related.length > 0) && (
            <section className="clinic-section clinic-section--alt">
              <div className="clinic-section-inner">
                <header className="clinic-section-header">
                  <span className="clinic-section-label">관련 글</span>
                  <h2 className="clinic-section-heading">진료 영역별 의료 정보</h2>
                  <p className="clinic-section-lede">
                    진료 전 궁금해할 만한 질문과 안내를 진료 영역별로 모았습니다.
                  </p>
                </header>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 48 }}>
                  {treatmentsWithRelated
                    .filter((t) => t.related.length > 0)
                    .map(({ treatment, related }) => {
                      return (
                        <div key={`group-${treatment.name}`}>
                          <div className="clinic-content-group-header">
                            <h3 className="clinic-content-group-heading">
                              {treatment.name}
                            </h3>
                            <span className="clinic-content-group-count">{related.length}편</span>
                          </div>
                          <div className="clinic-content-grid">
                            {related.map((content) => (
                              <ContentCard
                                key={content.id}
                                content={content}
                                hospitalSlug={params.slug}
                                hospitalName={hospital.name}
                              />
                            ))}
                          </div>
                        </div>
                      )
                    })}
                </div>

                <div style={{ marginTop: 32, textAlign: 'right' }}>
                  <Link
                    href={`/${params.slug}/contents`}
                    className="clinic-btn clinic-btn-secondary"
                  >
                    의료 정보 전체 보기
                  </Link>
                </div>
              </div>
            </section>
          )}
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
