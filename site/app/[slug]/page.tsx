import { Metadata } from 'next'
import Image from 'next/image'
import Link from 'next/link'
import { fetchHospital, fetchContents, TYPE_LABELS } from '@/lib/api'
import { notFound } from 'next/navigation'

interface Props {
  params: { slug: string }
}

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'
const DAY_LABELS: Record<string, string> = {
  mon: '월',
  tue: '화',
  wed: '수',
  thu: '목',
  fri: '금',
  sat: '토',
  sun: '일',
}
const SCHEMA_DAY_OF_WEEK: Record<string, string> = {
  mon: 'Monday',
  tue: 'Tuesday',
  wed: 'Wednesday',
  thu: 'Thursday',
  fri: 'Friday',
  sat: 'Saturday',
  sun: 'Sunday',
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.name}의 진료정보, 원장 소개, 의료 콘텐츠`
    return {
      title: `${hospital.name} | AI가 읽기 쉬운 병원 의료정보`,
      description,
      alternates: {
        canonical: `/${params.slug}`,
      },
      openGraph: {
        title: `${hospital.name} | AI가 읽기 쉬운 병원 의료정보`,
        description,
        url: `/${params.slug}`,
        type: 'website',
      },
    }
  } catch {
    return { title: 'AI가 읽기 쉬운 병원 의료정보' }
  }
}

export default async function HospitalPage({ params }: Props) {
  let hospital
  let contents
  try {
    ;[hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug),
    ])
  } catch {
    notFound()
  }

  const recentContents = contents.slice(0, 3)
  const sameAs = [
    hospital.website_url,
    hospital.blog_url,
    hospital.kakao_channel_url,
    hospital.google_business_profile_url,
    hospital.google_maps_url,
    hospital.naver_place_url,
  ].filter(Boolean)

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': ['MedicalClinic', 'LocalBusiness'],
    name: hospital.name,
    url: `${SITE_URL}/${params.slug}`,
    sameAs,
    address: {
      '@type': 'PostalAddress',
      streetAddress: hospital.address,
      addressCountry: 'KR',
    },
    telephone: hospital.phone,
    medicalSpecialty: hospital.specialties,
    openingHoursSpecification: Object.entries(hospital.business_hours || {}).map(([day, hours]) => ({
      '@type': 'OpeningHoursSpecification',
      dayOfWeek: SCHEMA_DAY_OF_WEEK[day] || day,
      description: String(hours),
    })),
    hasMap: hospital.google_maps_url || undefined,
    geo: hospital.latitude && hospital.longitude
      ? {
          '@type': 'GeoCoordinates',
          latitude: hospital.latitude,
          longitude: hospital.longitude,
        }
      : undefined,
    physician: {
      '@type': 'Physician',
      name: hospital.director_name,
      description: hospital.director_career,
    },
    availableService: (hospital.treatments || []).map((treatment) => ({
      '@type': 'MedicalProcedure',
      name: treatment.name,
      description: treatment.description,
    })),
  }

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, '\\u003c') }}
      />

      {/* Hero */}
      <section className="bg-blue-600 text-white py-20 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <h1 className="text-4xl md:text-5xl font-bold mb-4">{hospital.name}</h1>
          <p className="text-xl text-blue-100">
            {hospital.region.join(' ')} {hospital.specialties.join(' · ')} 전문 클리닉
          </p>
        </div>
      </section>

      {/* 원장 소개 */}
      <section className="bg-white py-16 px-6">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-2xl font-bold text-gray-800 mb-8 text-center">원장 소개</h2>
          <div className="bg-blue-50 rounded-2xl p-8">
            <h3 className="text-xl font-semibold text-blue-800 mb-2">{hospital.director_name} 원장</h3>
            <p className="text-gray-600 mb-4 whitespace-pre-line">{hospital.director_career}</p>
            {hospital.director_philosophy && (
              <blockquote className="border-l-4 border-blue-400 pl-4 italic text-gray-700">
                {'"'}{hospital.director_philosophy}{'"'}
              </blockquote>
            )}
          </div>
        </div>
      </section>

      {/* 진료 항목 */}
      {hospital.treatments && hospital.treatments.length > 0 && (
        <section className="bg-gray-50 py-16 px-6">
          <div className="max-w-4xl mx-auto">
            <h2 className="text-2xl font-bold text-gray-800 mb-8 text-center">진료 항목</h2>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              {hospital.treatments.map((treatment, idx) => (
                <div
                  key={idx}
                  className="bg-white rounded-xl p-4 text-center shadow-sm border border-blue-100 hover:border-blue-300 transition-colors"
                >
                  <span className="text-gray-700 font-medium">{treatment.name}</span>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* 진료 안내 */}
      <section className="bg-white py-16 px-6">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-2xl font-bold text-gray-800 mb-8 text-center">진료 안내</h2>
          <div className="grid md:grid-cols-3 gap-6">
            <div className="text-center">
              <div className="text-blue-600 text-3xl mb-2">📍</div>
              <h3 className="font-semibold text-gray-800 mb-1">주소</h3>
              <p className="text-gray-600 text-sm">{hospital.address}</p>
            </div>
            <div className="text-center">
              <div className="text-blue-600 text-3xl mb-2">📞</div>
              <h3 className="font-semibold text-gray-800 mb-1">전화</h3>
              <a href={`tel:${hospital.phone}`} className="text-blue-600 font-medium">
                {hospital.phone}
              </a>
            </div>
            <div className="text-center">
              <div className="text-blue-600 text-3xl mb-2">🕐</div>
              <h3 className="font-semibold text-gray-800 mb-1">진료시간</h3>
              {Object.entries(hospital.business_hours || {}).map(([day, hours]) => (
                <p key={day} className="text-gray-600 text-sm">
                  {DAY_LABELS[day] || day}: {hours}
                </p>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* 최근 콘텐츠 */}
      {recentContents.length > 0 && (
        <section className="bg-gray-50 py-16 px-6">
          <div className="max-w-4xl mx-auto">
            <div className="flex justify-between items-center mb-8">
              <h2 className="text-2xl font-bold text-gray-800">최근 의료 정보</h2>
              <Link
                href={`/${params.slug}/contents`}
                className="text-blue-600 hover:underline text-sm font-medium"
              >
                전체 보기 →
              </Link>
            </div>
            <div className="grid md:grid-cols-3 gap-6">
              {recentContents.map((content) => (
                <Link
                  key={content.id}
                  href={`/${params.slug}/contents/${content.id}`}
                  className="bg-white rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow"
                >
                  {content.image_url && (
                    <div className="relative h-40">
                      <Image
                        src={content.image_url}
                        alt={content.title}
                        fill
                        className="object-cover"
                      />
                    </div>
                  )}
                  <div className="p-4">
                    <span className="inline-block bg-blue-100 text-blue-700 text-xs font-medium px-2 py-1 rounded mb-2">
                      {TYPE_LABELS[content.content_type] || content.content_type}
                    </span>
                    <h3 className="font-semibold text-gray-800 text-sm line-clamp-2">{content.title}</h3>
                    <p className="text-gray-400 text-xs mt-2">
                      {content.published_at
                        ? new Date(content.published_at).toLocaleDateString('ko-KR')
                        : ''}
                    </p>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Footer */}
      <footer className="bg-gray-800 text-gray-400 py-8 px-6 text-center text-sm">
        <p>{hospital.name} · {hospital.address} · {hospital.phone}</p>
        <p className="mt-2">© {new Date().getFullYear()} {hospital.name}. All rights reserved.</p>
      </footer>
    </>
  )
}
