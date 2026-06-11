import { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'

import { fetchContents, fetchHospital, resolveAssetUrl, HospitalNotFoundError, type ContentSummary } from '@/lib/api'
import { getApiBase } from '@/lib/config'
import { canonicalBase } from '@/lib/site-url'
import {
  buildTreatmentSlug,
  findTreatmentBySlug,
  inferPillarTreatment,
  normalizeTreatmentSlug,
} from '@/lib/treatment-slug'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../../_components/Breadcrumb'
import { ClinicFooter } from '../../_components/ClinicFooter'
import { ClinicHeader } from '../../_components/ClinicHeader'
import { ContentCard } from '../../_components/ContentCard'
import { JsonLd } from '../../_components/JsonLd'
import { pickIconForTreatment } from '../../_components/MedicalIcons'

interface Props {
  params: Promise<{ slug: string; treatmentSlug: string }>
}

export const revalidate = 3600

export async function generateStaticParams() {
  try {
    const apiBase = getApiBase(false)
    if (!apiBase) return []
    const res = await fetch(`${apiBase}/hospitals`, { next: { revalidate: 3600 } })
    if (!res.ok) return []
    const hospitals = (await res.json()) as Array<{ slug: string }>

    const params: Array<{ slug: string; treatmentSlug: string }> = []
    for (const h of hospitals) {
      try {
        const detail = await fetchHospital(h.slug)
        for (const treatment of detail.treatments || []) {
          const treatmentSlug = buildTreatmentSlug(treatment.name)
          if (treatmentSlug) params.push({ slug: h.slug, treatmentSlug })
        }
      } catch {
        // hospital fetch 실패는 무시 — 다른 병원은 빌드 계속.
      }
    }
    return params
  } catch {
    return []
  }
}

export async function generateMetadata({ params: paramsPromise }: Props): Promise<Metadata> {
  const params = await paramsPromise
  try {
    const hospital = await fetchHospital(params.slug)
    const treatment = findTreatmentBySlug(hospital.treatments || [], params.treatmentSlug)
    if (!treatment) return { title: '진료 영역' }
    const treatmentSlug = normalizeTreatmentSlug(params.treatmentSlug)
    const region = hospital.region?.join(' ') ?? ''
    const description = `${hospital.name} ${treatment.name} 진료 안내 — 환자가 자주 묻는 질문과 진료 단계, 회복 정보를 ${region} 의료진이 정리합니다.`
    const canonicalUrl = `${canonicalBase(hospital)}/${params.slug}/treatments/${treatmentSlug}`
    return {
      title: `${treatment.name} | ${hospital.name}`,
      description,
      alternates: {
        canonical: canonicalUrl,
      },
      openGraph: {
        title: `${treatment.name} | ${hospital.name}`,
        description,
        url: canonicalUrl,
        type: 'website',
        images: (() => {
          const photo = resolveAssetUrl(hospital.director_photo_url)
          return photo ? [{ url: photo }] : undefined
        })(),
      },
      twitter: {
        card: 'summary_large_image',
        title: `${treatment.name} | ${hospital.name}`,
        description,
        images: (() => {
          const photo = resolveAssetUrl(hospital.director_photo_url)
          return photo ? [photo] : undefined
        })(),
      },
    }
  } catch {
    return { title: '진료 영역' }
  }
}

export default async function TreatmentPillarPage({ params: paramsPromise }: Props) {
  const params = await paramsPromise
  let hospital
  let contents: ContentSummary[]
  try {
    ;[hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 500),
    ])
  } catch (e) {
    if (e instanceof HospitalNotFoundError) notFound()
    throw e
  }

  const treatments = hospital.treatments || []
  const treatment = findTreatmentBySlug(treatments, params.treatmentSlug)
  if (!treatment) notFound()

  const canonicalTreatmentSlug = buildTreatmentSlug(treatment.name)
  const treatmentName = treatment.name
  const lowerName = treatmentName.toLowerCase()
  const relatedContents = contents.filter((content) => {
    const inferred = inferPillarTreatment(treatments, content)
    if (inferred && buildTreatmentSlug(inferred.name) === canonicalTreatmentSlug) return true
    const haystack = `${content.title ?? ''} ${content.meta_description ?? ''} ${content.faq_question ?? ''}`.toLowerCase()
    return haystack.includes(lowerName)
  })

  const breadcrumbItems = [
    { label: '홈', href: `/${params.slug}` },
    { label: '진료 영역', href: `/${params.slug}/treatments` },
    { label: treatmentName },
  ]

  const base = canonicalBase(hospital)
  const pageUrl = `${base}/${params.slug}/treatments/${canonicalTreatmentSlug}`

  const collectionJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'CollectionPage',
    name: `${hospital.name} ${treatmentName}`,
    url: pageUrl,
    isPartOf: {
      '@type': 'MedicalClinic',
      '@id': `${base}/${params.slug}#clinic`,
      name: hospital.name,
      url: `${base}/${params.slug}`,
    },
    about: {
      '@type': 'MedicalProcedure',
      name: treatmentName,
      description: treatment.description || undefined,
      performer: {
        '@type': 'MedicalClinic',
        '@id': `${base}/${params.slug}#clinic`,
        name: hospital.name,
      },
    },
    mainEntity: {
      '@type': 'ItemList',
      itemListElement: relatedContents.map((content, idx) => ({
        '@type': 'ListItem',
        position: idx + 1,
        url: `${base}/${params.slug}/contents/${content.id}`,
        name: content.title,
      })),
    },
  }

  const { Icon, hue } = pickIconForTreatment(treatmentName)

  return (
    <>
      <JsonLd data={[collectionJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, base)]} />
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
              <span className="clinic-section-label">진료 영역</span>
              <h1 className="clinic-library-hero-title">
                <span
                  className={`clinic-treatment-card-icon hue-${hue}`}
                  style={{ width: 36, height: 36, marginRight: 12, verticalAlign: 'middle' }}
                  aria-hidden="true"
                >
                  <Icon />
                </span>
                {treatmentName}
              </h1>
              <p className="clinic-library-hero-meta">
                <strong>{hospital.name}</strong>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.specialties?.join(' · ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.region?.join(' ')}</span>
              </p>
              {treatment.description && (
                <p className="clinic-section-lede" style={{ marginTop: 16, maxWidth: 720, fontSize: 14 }}>
                  {treatment.description}
                </p>
              )}
            </div>
          </section>

          <section className="clinic-section">
            <div className="clinic-section-inner">
              {relatedContents.length === 0 ? (
                <div className="clinic-empty">
                  <span className="clinic-empty-title">관련 의료 정보가 준비 중입니다</span>
                  <p>{treatmentName}에 대한 환자 안내 글이 곧 발행됩니다.</p>
                  <Link
                    href={`/${params.slug}/contents`}
                    className="clinic-btn clinic-btn-secondary"
                    style={{ marginTop: 16 }}
                  >
                    전체 의료 정보 보기
                  </Link>
                </div>
              ) : (
                <>
                  <header className="clinic-section-header">
                    <span className="clinic-section-label">관련 콘텐츠 {relatedContents.length}편</span>
                    <h2 className="clinic-section-heading">{treatmentName} 진료 안내 글 모음</h2>
                    <p className="clinic-section-lede">
                      {treatmentName}와 관련해 환자가 자주 묻는 질문, 질환 정보, 진료 단계를 모았습니다.
                    </p>
                  </header>
                  <div className="clinic-content-grid">
                    {relatedContents.map((content) => (
                      <ContentCard
                        key={content.id}
                        content={content}
                        hospitalSlug={params.slug}
                        hospitalName={hospital.name}
                      />
                    ))}
                  </div>
                  <div style={{ marginTop: 32, textAlign: 'right' }}>
                    <Link
                      href={`/${params.slug}/contents`}
                      className="clinic-btn clinic-btn-secondary"
                    >
                      의료 정보 전체 보기
                    </Link>
                  </div>
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
