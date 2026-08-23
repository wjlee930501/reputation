import Image from 'next/image'

import { resolveAssetUrl, type HospitalPhoto } from '@/lib/api'
import { previewCountLabel } from '@/lib/clinic-counters'
import { selectClinicGalleryPhotos, type ClinicGalleryPolicy } from '@/lib/clinic-design'

interface Props {
  photos: HospitalPhoto[]
  /**
   * 표면별 장수 정책. 기본값을 두지 않는다 — 홈과 `/visit`이 컴포넌트 기본값과
   * 호출부 인자로 규칙을 반씩 나눠 갖고 있었고, 그래서 두 화면의 정책이 갈렸다.
   */
  policy: ClinicGalleryPolicy
}

const TYPE_LABELS: Record<HospitalPhoto['source_type'], string> = {
  PHOTO_DOCTOR: '원장',
  PHOTO_CLINIC_EXTERIOR: '외관',
  PHOTO_CLINIC_INTERIOR: '내부',
  PHOTO_TREATMENT_ROOM: '진료/시술실',
}

export function ClinicGallery({ photos, policy }: Props) {
  const selection = selectClinicGalleryPhotos(photos, policy.previewLimit)
  const visible = selection.photos
  if (visible.length < policy.minimumPhotoCount) return null

  const previewLabel = previewCountLabel(visible.length, selection.total, '장')

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
        {previewLabel ? (
          <p className="clinic-gallery-summary">등록된 공간 사진 {previewLabel}을 보여드립니다.</p>
        ) : null}
      </div>
    </section>
  )
}
