'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useEffect, useState } from 'react'

import { buildWeeklyHoursRows } from '@/lib/business-hours'
import { fetchHospital, type Hospital } from '@/lib/api'
import { buildClinicThemeStyle } from '@/lib/clinic-theme'
import { safeExternalHref } from '@/lib/safe-url'
import { canonicalHospitalUrl } from '@/lib/site-url'

import { ClinicFooter } from './_components/ClinicFooter'
import { ClinicHeader } from './_components/ClinicHeader'

export default function ClinicNotFound() {
  const { slug } = useParams<{ slug: string }>()
  const [hospital, setHospital] = useState<Hospital | null>(null)
  const [resolved, setResolved] = useState(false)

  useEffect(() => {
    let active = true
    void fetchHospital(slug)
      .then((value) => {
        if (active) setHospital(value)
      })
      .catch(() => {
        // Unknown slugs retain the global Re:putation 404 experience. Other fetch
        // failures also fail closed instead of fabricating clinic contact details.
      })
      .finally(() => {
        if (active) setResolved(true)
      })
    return () => {
      active = false
    }
  }, [slug])

  if (!resolved || !hospital) return <GlobalNotFoundFallback loading={!resolved} />

  const hospitalRootUrl = canonicalHospitalUrl(hospital, slug)
  const hours = buildWeeklyHoursRows(hospital.business_hours).filter((row) => row.value).slice(0, 3)
  const mapsUrl = safeExternalHref(hospital.google_maps_url)

  return (
    <div className="clinic-shell clinic-shell--editorial" style={buildClinicThemeStyle(hospital)}>
      <ClinicHeader
        hospitalName={hospital.name}
        hospitalRootUrl={hospitalRootUrl}
        region={hospital.region}
        specialties={hospital.specialties}
        phone={hospital.phone}
        websiteUrl={hospital.website_url}
        logoUrl={hospital.logo_url}
        googleMapsUrl={hospital.google_maps_url}
      />
      <main id="main-content" className="clinic-library-hero">
        <div className="clinic-library-hero-inner">
          <span className="clinic-section-label">페이지 안내</span>
          <h1 className="clinic-library-hero-title">요청하신 병원 페이지를 찾을 수 없습니다</h1>
          <p className="clinic-section-lede">
            주소가 바뀌었거나 콘텐츠가 비공개되었을 수 있습니다. 아래 병원 공식 정보로 계속 확인해 주세요.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link className="clinic-btn clinic-btn-cta" href={hospitalRootUrl}>병원 홈</Link>
            <a className="clinic-btn clinic-btn-secondary" href={`tel:${hospital.phone}`}>
              전화 {hospital.phone}
            </a>
            <Link className="clinic-btn clinic-btn-secondary" href={`${hospitalRootUrl}/visit#clinic-hours`}>
              전체 진료시간
            </Link>
            {mapsUrl && <a className="clinic-btn clinic-btn-secondary" href={mapsUrl}>지도에서 보기</a>}
          </div>
          <dl className="mt-8 grid max-w-2xl gap-3 border-t border-slate-200 pt-5 text-sm sm:grid-cols-3">
            {hours.length > 0 ? hours.map((row) => (
              <div key={row.key}>
                <dt className="font-semibold text-slate-700">{row.label}</dt>
                <dd className="mt-1 text-slate-600">{row.value}</dd>
              </div>
            )) : (
              <div>
                <dt className="font-semibold text-slate-700">진료시간</dt>
                <dd className="mt-1 text-slate-600">전화로 확인해 주세요.</dd>
              </div>
            )}
          </dl>
        </div>
      </main>
      <ClinicFooter
        hospitalName={hospital.name}
        directorName={hospital.director_name}
        address={hospital.address}
        phone={hospital.phone}
        websiteUrl={hospital.website_url}
      />
    </div>
  )
}

function GlobalNotFoundFallback({ loading }: { loading: boolean }) {
  return (
    <main id="main-content" className="min-h-screen flex items-center justify-center bg-slate-50 px-6">
      <div className="text-center max-w-md" aria-live="polite">
        <p className="text-sm font-semibold text-blue-600 mb-2">404</p>
        <h1 className="text-2xl font-bold text-slate-800 mb-3">페이지를 찾을 수 없습니다</h1>
        <p className="text-slate-500 mb-8 text-sm leading-relaxed">
          {loading ? '병원 정보를 확인하고 있습니다.' : '주소가 잘못되었거나 페이지가 이동·삭제되었을 수 있습니다.'}
        </p>
        <Link href="/" className="inline-block bg-blue-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-blue-700 transition-colors">
          Re:putation 홈으로 이동
        </Link>
      </div>
    </main>
  )
}
