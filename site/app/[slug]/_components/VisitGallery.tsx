import { CLINIC_IMAGE_SIZES } from '@/lib/clinic-image-delivery'
import Image from 'next/image'

import { resolveAssetUrl, type HospitalPhoto } from '@/lib/api'
import { countLabel } from '@/lib/clinic-counters'
import { CLINIC_GALLERY_CATEGORY_ORDER, type ClinicGalleryPolicy } from '@/lib/clinic-design'

import { GALLERY_TYPE_LABELS } from './ClinicGallery'

interface Props {
  photos: HospitalPhoto[]
  /** 홈과 같은 정책 함수에서 받는다 — 두 화면이 게이트를 반씩 나눠 갖지 않는다. */
  policy: ClinicGalleryPolicy
}

/**
 * `/visit`의 전체 공간 사진.
 *
 * 홈 갤러리는 미리보기(6~8장 상한)라서 승인된 사진이 그보다 많은 병원에서는 "전체
 * 보기"가 갈 곳이 필요하다. 방문 준비가 목적인 이 화면은 상한 없이 분류별로 전부
 * 보여준다 — 외관/내부/진료·시술실을 나누면 "주차장에서 어디로 들어가는가"를 사진
 * 순서만으로 읽을 수 있다.
 */
export function VisitGallery({ photos, policy }: Props) {
  const usable = photos.filter(
    (photo) =>
      photo.asset_kind !== 'EDITORIAL_GRAPHIC' &&
      (!photo.approved_usage || photo.approved_usage.includes('GALLERY')) &&
      Boolean(resolveAssetUrl(photo.url)),
  )
  if (usable.length < policy.minimumPhotoCount) return null

  const groups = CLINIC_GALLERY_CATEGORY_ORDER.map((sourceType) => ({
    sourceType,
    label: GALLERY_TYPE_LABELS[sourceType],
    photos: usable.filter((photo) => photo.source_type === sourceType),
  })).filter((group) => group.photos.length > 0)

  return (
    <section id="gallery" className="clinic-section clinic-section--alt">
      <div className="clinic-section-inner">
        <header className="clinic-section-head">
          <h2 className="clinic-section-title">병원 공간</h2>
          <p className="clinic-section-note">
            승인된 공간 사진 {countLabel(usable.length, '장')}을 분류별로 모두 보여드립니다.
          </p>
        </header>

        {groups.map((group) => (
          <div key={group.sourceType} className="clinic-visit-gallery-group">
            <h3 className="clinic-visit-gallery-heading">
              {group.label}
              <span className="clinic-visit-gallery-count">{countLabel(group.photos.length, '장')}</span>
            </h3>
            <div className="clinic-gallery-grid">
              {group.photos.map((photo) => {
                const url = resolveAssetUrl(photo.url)
                if (!url) return null
                return (
                  <figure key={photo.id} className="clinic-gallery-item">
                    <Image
                      src={url}
                      alt={photo.title}
                      fill
                      sizes={CLINIC_IMAGE_SIZES.gallery}
                      loading="lazy"
                      style={{ objectFit: 'cover' }}
                    />
                    <figcaption className="clinic-gallery-caption">
                      <span className="clinic-gallery-caption-title">{photo.title}</span>
                    </figcaption>
                  </figure>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
