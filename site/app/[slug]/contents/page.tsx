import { Metadata } from 'next'
import Image from 'next/image'
import Link from 'next/link'
import { notFound } from 'next/navigation'

import { fetchContents, fetchHospital, TYPE_LABELS } from '@/lib/api'

interface Props {
  params: { slug: string }
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.name}의 지역 진료 FAQ, 질환 가이드, 치료 안내 콘텐츠`
    return {
      title: `${hospital.name} 의료 정보`,
      description,
      alternates: {
        canonical: `/${params.slug}/contents`,
      },
      openGraph: {
        title: `${hospital.name} 의료 정보`,
        description,
        url: `/${params.slug}/contents`,
        type: 'website',
      },
    }
  } catch {
    return { title: 'AEO 의료정보' }
  }
}

export default async function ContentsPage({ params }: Props) {
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

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'CollectionPage',
    name: `${hospital.name} 의료 정보`,
    about: hospital.specialties,
    isPartOf: {
      '@type': 'WebSite',
      name: hospital.name,
      url: `/${params.slug}`,
    },
    hasPart: contents.map((content) => ({
      '@type': 'Article',
      headline: content.title,
      url: `/${params.slug}/contents/${content.id}`,
      datePublished: content.published_at || content.scheduled_date,
    })),
  }

  const grouped = contents.reduce<Record<string, typeof contents>>((acc, content) => {
    const key = content.content_type
    acc[key] = acc[key] || []
    acc[key].push(content)
    return acc
  }, {})

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, '\\u003c') }}
      />
      <div className="min-h-screen bg-gray-50">
        <div className="bg-blue-600 text-white py-12 px-6">
          <div className="max-w-4xl mx-auto">
            <Link href={`/${params.slug}`} className="text-blue-200 hover:text-white text-sm mb-2 inline-block">
              홈으로
            </Link>
            <h1 className="text-3xl font-bold">{hospital.name} 의료 정보</h1>
            <p className="text-blue-100 mt-2">
              {hospital.region?.join(' ')} {hospital.specialties?.join(' · ')} 관련 질문과 진료 안내
            </p>
          </div>
        </div>

        <div className="max-w-4xl mx-auto px-6 py-8 space-y-10">
          {contents.length === 0 ? (
            <p className="text-center text-gray-400 py-16">콘텐츠가 없습니다.</p>
          ) : (
            Object.entries(grouped).map(([type, items]) => (
              <section key={type}>
                <h2 className="text-xl font-bold text-gray-900 mb-4">
                  {TYPE_LABELS[type] || type}
                </h2>
                <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
                  {items.map((content) => (
                    <Link
                      key={content.id}
                      href={`/${params.slug}/contents/${content.id}`}
                      className="bg-white rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow"
                    >
                      {content.image_url ? (
                        <div className="relative h-40">
                          <Image
                            src={content.image_url}
                            alt={content.title}
                            fill
                            className="object-cover"
                          />
                        </div>
                      ) : (
                        <div className="h-40 bg-blue-50 flex items-center justify-center text-blue-700 text-sm font-medium">
                          {hospital.name}
                        </div>
                      )}
                      <div className="p-4">
                        <span className="inline-block bg-blue-100 text-blue-700 text-xs font-medium px-2 py-1 rounded mb-2">
                          {TYPE_LABELS[content.content_type] || content.content_type}
                        </span>
                        <h3 className="font-semibold text-gray-800 text-sm line-clamp-2 mb-2">
                          {content.title}
                        </h3>
                        {content.meta_description && (
                          <p className="text-gray-500 text-xs line-clamp-2 mb-2">
                            {content.meta_description}
                          </p>
                        )}
                        <p className="text-gray-400 text-xs">
                          {content.published_at
                            ? new Date(content.published_at).toLocaleDateString('ko-KR')
                            : content.scheduled_date}
                        </p>
                      </div>
                    </Link>
                  ))}
                </div>
              </section>
            ))
          )}
        </div>
      </div>
    </>
  )
}
