import Image from 'next/image'
import Link from 'next/link'

import { resolveAssetUrl, type HospitalPhoto } from '@/lib/api'
import { countLabel, previewCountLabel } from '@/lib/clinic-counters'
import { selectClinicGalleryPhotos, type ClinicGalleryPolicy } from '@/lib/clinic-design'

import { ChevronRightIcon } from './icons'

interface Props {
  photos: HospitalPhoto[]
  /**
   * 표면별 장수 정책. 기본값을 두지 않는다 — 홈과 `/visit`이 컴포넌트 기본값과
   * 호출부 인자로 규칙을 반씩 나눠 갖고 있었고, 그래서 두 화면의 정책이 갈렸다.
   */
  policy: ClinicGalleryPolicy
  /** 히어로가 이미 쓰는 사진. 첫 화면과 같은 사진을 갤러리에 다시 걸지 않는다. */
  excludeUrl?: string | null
  /** 전체 사진을 볼 수 있는 곳. 있으면 문장 대신 링크로 안내한다. */
  allPhotosHref?: string | null
}

export const GALLERY_TYPE_LABELS: Record<HospitalPhoto['source_type'], string> = {
  PHOTO_DOCTOR: '원장',
  PHOTO_CLINIC_EXTERIOR: '외관',
  PHOTO_CLINIC_INTERIOR: '내부',
  PHOTO_TREATMENT_ROOM: '진료·시술실',
}

export function ClinicGallery({ photos, policy, excludeUrl = null, allPhotosHref = null }: Props) {
  const selection = selectClinicGalleryPhotos(photos, policy.previewLimit, { excludeUrl })
  const visible = selection.photos
  if (visible.length < policy.minimumPhotoCount) return null

  const hasMore = selection.total > visible.length

  return (
    <section id="gallery" className="hub-section">
      <div className="hub-container">
        <header className="hub-section-head">
          <h2 className="hub-section-title">병원 공간</h2>
        </header>

        {/* 격자 모양은 CSS가 장수(data-count)로 정한다 — 어떤 장수에서도 홀로 남는 타일이 없다. */}
        <div className="hub-gallery" data-count={visible.length}>
          {visible.map((photo, index) => {
            const url = resolveAssetUrl(photo.url)
            if (!url) return null
            // 첫 타일은 장수에 따라 두 칸을 차지한다 — 크기는 CSS가 정하므로 sizes만 맞춘다.
            const lead = index === 0 && visible.length >= 3
            return (
              <figure key={photo.id} className="hub-gallery-item">
                <Image
                  src={url}
                  alt={photo.title}
                  fill
                  sizes={
                    lead
                      ? '(max-width: 719px) 100vw, (max-width: 1023px) 100vw, 800px'
                      : '(max-width: 719px) 50vw, (max-width: 1023px) 50vw, 400px'
                  }
                />
                <figcaption className="hub-gallery-caption">
                  <span className="hub-gallery-caption-type">
                    {GALLERY_TYPE_LABELS[photo.source_type]}
                  </span>
                  <span className="hub-gallery-caption-title">{photo.title}</span>
                </figcaption>
              </figure>
            )
          })}
        </div>

        {hasMore ? (
          allPhotosHref ? (
            <Link href={allPhotosHref} className="hub-more">
              공간 사진 전체 {countLabel(selection.total, '장')} 보기
              <ChevronRightIcon className="hub-icon hub-icon--sm" style={{ color: 'currentColor' }} />
            </Link>
          ) : (
            <p className="hub-section-note" style={{ marginTop: 'var(--clinic-space-6)' }}>
              등록된 공간 사진 {previewCountLabel(visible.length, selection.total, '장')}을
              보여드립니다.
            </p>
          )
        ) : null}
      </div>
    </section>
  )
}
