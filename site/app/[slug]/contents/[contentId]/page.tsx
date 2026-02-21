import { Metadata } from 'next'
import Image from 'next/image'
import Link from 'next/link'
import { notFound } from 'next/navigation'
import { fetchHospital, fetchContents, fetchContent, TYPE_LABELS } from '@/lib/api'
import ReactMarkdown from 'react-markdown'

interface Props {
  params: { slug: string; contentId: string }
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  try {
    const [hospital, content] = await Promise.all([
      fetchHospital(params.slug),
      fetchContent(params.slug, params.contentId),
    ])
    return {
      title: `${content.title} | ${hospital.name}`,
      description: `${hospital.name}의 ${TYPE_LABELS[content.content_type] || ''} 콘텐츠`,
    }
  } catch {
    return { title: 'AEO 의료정보' }
  }
}

export default async function ContentDetailPage({ params }: Props) {
  let hospital, content, allContents
  try {
    ;[hospital, content, allContents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContent(params.slug, params.contentId),
      fetchContents(params.slug),
    ])
  } catch {
    notFound()
  }

  const related = allContents
    .filter(c => c.id !== content.id && c.content_type === content.content_type)
    .slice(0, 3)

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: content.title,
    author: {
      '@type': 'Physician',
      name: hospital.director_name,
    },
    publisher: {
      '@type': 'MedicalClinic',
      name: hospital.name,
    },
    datePublished: content.published_at || content.scheduled_date,
    image: content.image_url,
  }

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <div className="min-h-screen bg-gray-50">
        {/* Header */}
        <div className="bg-blue-600 text-white py-8 px-6">
          <div className="max-w-5xl mx-auto">
            <Link href={`/${params.slug}/contents`} className="text-blue-200 hover:text-white text-sm mb-2 inline-block">
              ← 목록으로
            </Link>
            <span className="inline-block bg-blue-500 text-white text-xs font-medium px-3 py-1 rounded-full mt-2">
              {TYPE_LABELS[content.content_type] || content.content_type}
            </span>
          </div>
        </div>

        <div className="max-w-5xl mx-auto px-6 py-8 flex flex-col lg:flex-row gap-8">
          {/* Main content */}
          <article className="flex-1 bg-white rounded-2xl overflow-hidden shadow-sm">
            {content.image_url && (
              <div className="relative h-64 w-full">
                <Image
                  src={content.image_url}
                  alt={content.title}
                  fill
                  className="object-cover"
                />
              </div>
            )}
            <div className="p-8">
              <h1 className="text-2xl font-bold text-gray-900 mb-2">{content.title}</h1>
              <div className="flex items-center gap-3 text-gray-400 text-sm mb-8 pb-6 border-b">
                <span>{hospital.director_name} 원장</span>
                <span>·</span>
                <span>
                  {content.published_at
                    ? new Date(content.published_at).toLocaleDateString('ko-KR')
                    : content.scheduled_date}
                </span>
              </div>
              <div className="prose prose-blue max-w-none text-gray-700">
                <ReactMarkdown>{content.body}</ReactMarkdown>
              </div>
            </div>
          </article>

          {/* Sidebar */}
          <aside className="w-full lg:w-72 shrink-0">
            {/* Hospital info */}
            <div className="bg-white rounded-2xl p-6 shadow-sm mb-6">
              <h2 className="font-bold text-gray-800 mb-4">{hospital.name}</h2>
              <p className="text-sm text-gray-600 mb-1">📍 {hospital.address}</p>
              <a href={`tel:${hospital.phone}`} className="text-sm text-blue-600 font-medium block mb-4">
                📞 {hospital.phone}
              </a>
              <Link
                href={`/${params.slug}`}
                className="block w-full bg-blue-600 text-white text-center py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
              >
                병원 홈페이지
              </Link>
            </div>

            {/* Related contents */}
            {related.length > 0 && (
              <div className="bg-white rounded-2xl p-6 shadow-sm">
                <h2 className="font-bold text-gray-800 mb-4">관련 콘텐츠</h2>
                <div className="space-y-4">
                  {related.map(r => (
                    <Link
                      key={r.id}
                      href={`/${params.slug}/contents/${r.id}`}
                      className="flex gap-3 hover:bg-gray-50 rounded-lg p-2 -mx-2 transition-colors"
                    >
                      {r.image_url && (
                        <div className="relative w-16 h-16 shrink-0 rounded-lg overflow-hidden">
                          <Image src={r.image_url} alt={r.title} fill className="object-cover" />
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-800 line-clamp-2">{r.title}</p>
                        <p className="text-xs text-gray-400 mt-1">
                          {r.published_at
                            ? new Date(r.published_at).toLocaleDateString('ko-KR')
                            : r.scheduled_date}
                        </p>
                      </div>
                    </Link>
                  ))}
                </div>
              </div>
            )}
          </aside>
        </div>
      </div>
    </>
  )
}
