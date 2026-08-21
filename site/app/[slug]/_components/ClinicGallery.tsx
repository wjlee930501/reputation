import Image from 'next/image'

import { resolveAssetUrl, type HospitalPhoto } from '@/lib/api'
import { selectClinicGalleryPhotos } from '@/lib/clinic-design'

interface Props {
  photos: HospitalPhoto[]
  minimumPhotoCount?: number
  previewLimit?: number
}

const TYPE_LABELS: Record<HospitalPhoto['source_type'], string> = {
  PHOTO_DOCTOR: '원장',
  PHOTO_CLINIC_EXTERIOR: '외관',
  PHOTO_CLINIC_INTERIOR: '내부',
  PHOTO_TREATMENT_ROOM: '진료/시술실',
}

export function ClinicGallery({ photos, minimumPhotoCount = 3, previewLimit = 6 }: Props) {
  const selection = selectClinicGalleryPhotos(photos, previewLimit)
  const visible = selection.photos
  // 홈은 기본 3장 게이트를 유지하고, /visit만 명시적으로 1장부터 공간 안내를 노출한다.
  if (visible.length < minimumPhotoCount) return null

  return (
    <section className="clinic-section">
      <div className="clinic-section-inner">
        <header className="clinic-section-header">
          <span className="clinic-section-label">병원 둘러보기</span>
          <h2 className="clinic-section-heading">병원 공간</h2>
          <p className="clinic-section-lede">
            진료를 받기 전에 병원 외관과 진료 공간을 미리 확인할 수 있습니다.
          </p>
        </header>

        <div className={`clinic-gallery-grid${visible.length < 3 ? ' clinic-gallery-grid--sparse' : ''}`}>
          {visible.map((photo) => {
            const url = resolveAssetUrl(photo.url)
            if (!url) return null
            return (
              <figure key={photo.id} className="clinic-gallery-item">
                <Image
                  src={url}
                  alt={photo.title}
                  fill
                  sizes="(max-width: 720px) 100vw, (max-width: 1080px) 50vw, 360px"
                  style={{ objectFit: 'cover' }}
                />
                <figcaption className="clinic-gallery-caption">
                  <span className="clinic-gallery-caption-type">{TYPE_LABELS[photo.source_type]}</span>
                  <span className="clinic-gallery-caption-title">{photo.title}</span>
                </figcaption>
              </figure>
            )
          })}
        </div>
        {selection.remaining > 0 ? (
          <p className="clinic-gallery-summary">
            등록된 공간 사진 {selection.total}장 중 대표 {visible.length}장을 보여드립니다.
          </p>
        ) : null}
      </div>
    </section>
  )
}
