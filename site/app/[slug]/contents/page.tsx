'use client'

import { useEffect, useState } from 'react'
import Image from 'next/image'
import Link from 'next/link'
import { fetchContents, fetchHospital, TYPE_LABELS, ContentItem, Hospital } from '@/lib/api'
import { useParams } from 'next/navigation'

const ALL_TYPES = ['ALL', 'FAQ', 'DISEASE', 'TREATMENT', 'COLUMN', 'HEALTH', 'LOCAL', 'NOTICE']

export default function ContentsPage() {
  const params = useParams()
  const slug = params.slug as string
  const [hospital, setHospital] = useState<Hospital | null>(null)
  const [contents, setContents] = useState<ContentItem[]>([])
  const [activeType, setActiveType] = useState('ALL')

  useEffect(() => {
    Promise.all([fetchHospital(slug), fetchContents(slug)]).then(([h, c]) => {
      setHospital(h)
      setContents(c)
    })
  }, [slug])

  const filtered = activeType === 'ALL' ? contents : contents.filter(c => c.content_type === activeType)

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-blue-600 text-white py-12 px-6">
        <div className="max-w-4xl mx-auto">
          <Link href={`/${slug}`} className="text-blue-200 hover:text-white text-sm mb-2 inline-block">
            ← 홈으로
          </Link>
          <h1 className="text-3xl font-bold">{hospital?.name} 의료 정보</h1>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-6 py-8">
        {/* Type filter */}
        <div className="flex flex-wrap gap-2 mb-8">
          {ALL_TYPES.map(type => (
            <button
              key={type}
              onClick={() => setActiveType(type)}
              className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
                activeType === type
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-600 border border-gray-200 hover:border-blue-300'
              }`}
            >
              {type === 'ALL' ? '전체' : TYPE_LABELS[type] || type}
            </button>
          ))}
        </div>

        {/* Content grid */}
        {filtered.length === 0 ? (
          <p className="text-center text-gray-400 py-16">콘텐츠가 없습니다.</p>
        ) : (
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filtered.map(content => (
              <Link
                key={content.id}
                href={`/${slug}/contents/${content.id}`}
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
                  <div className="h-40 bg-blue-50 flex items-center justify-center text-blue-200 text-4xl">
                    🏥
                  </div>
                )}
                <div className="p-4">
                  <span className="inline-block bg-blue-100 text-blue-700 text-xs font-medium px-2 py-1 rounded mb-2">
                    {TYPE_LABELS[content.content_type] || content.content_type}
                  </span>
                  <h3 className="font-semibold text-gray-800 text-sm line-clamp-2 mb-2">
                    {content.title}
                  </h3>
                  <p className="text-gray-400 text-xs">
                    {content.published_at
                      ? new Date(content.published_at).toLocaleDateString('ko-KR')
                      : content.scheduled_date}
                  </p>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
