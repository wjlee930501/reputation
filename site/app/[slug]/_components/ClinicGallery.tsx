import Image from 'next/image'

import { resolveAssetUrl, type HospitalPhoto } from '@/lib/api'

interface Props {
  photos: HospitalPhoto[]
}

const TYPE_LABELS: Record<HospitalPhoto['source_type'], string> = {
  PHOTO_DOCTOR: '원장',
  PHOTO_CLINIC_EXTERIOR: '외관',
  PHOTO_CLINIC_INTERIOR: '내부',
  PHOTO_TREATMENT_ROOM: '진료/시술실',
}

const NON_DOCTOR_TYPES: Array<HospitalPhoto['source_type']> = [
  'PHOTO_CLINIC_EXTERIOR',
  'PHOTO_CLINIC_INTERIOR',
  'PHOTO_TREATMENT_ROOM',
]

export function ClinicGallery({ photos }: Props) {
  // 원장 사진은 DoctorIntro에서 노출하므로 갤러리에선 제외.
  const visible = photos.filter((p) => NON_DOCTOR_TYPES.includes(p.source_type))
  // P0-3: 사진이 3장 미만이면 갤러리 섹션 자체를 숨긴다. 1~2장 grid는 휑해서 "미완성" 인상을 준다.
  if (visible.length < 3) return null

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

        <div className="clinic-gallery-grid">
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
      </div>
    </section>
  )
}
