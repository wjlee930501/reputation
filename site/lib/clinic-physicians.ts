import { selectDoctorRole } from './clinic-design.ts'
import type { DirectorCredentials, Hospital, HospitalPhysician } from './hospital-payload.ts'
import { selectUnlinkedVerifiedDoctorImage, selectVerifiedDoctorImage } from './clinic-theme.ts'

/**
 * 화면·구조화 데이터·llms.txt가 함께 쓰는 의료진 한 명.
 *
 * `physicians[]`를 내리지 않는 구버전 응답에서는 `director_*` 한 벌에서 한 명을
 * 합성한다. 두 경로가 갈리면 화면과 JSON-LD가 서로 다른 사람을 주장하게 되므로
 * 선택을 여기 한 곳에 둔다.
 */
export interface ClinicPhysician {
  id: string
  name: string
  /** 직함. 이 사람의 유일한 역할 라벨이다 (없으면 null). */
  title: string | null
  specialties: string[]
  career: string | null
  credentials: DirectorCredentials | null
  /** 검증된 실제 인물 사진만 들어온다. 없으면 null — 생성 인물을 대신 넣지 않는다. */
  photoUrl: string | null
  isRepresentative: boolean
}

/** 카드 그리드가 쓰는 배치. 1명은 인물 중심 분할, 2~3명은 2열, 4명 이상은 3열. */
export type ClinicPhysicianLayout = 'solo' | 'pair' | 'grid'

/** 약력이 이 길이를 넘으면 `약력 더보기` 디스클로저로 접는다. */
export const PHYSICIAN_CAREER_CLAMP = 280

/** 카드 한 장에 노출하는 진료영역 칩 상한. */
export const PHYSICIAN_CHIP_LIMIT = 6

type PhysicianSource = Pick<
  Hospital,
  | 'director_name'
  | 'director_career'
  | 'director_credentials'
  | 'specialties'
  | 'photos'
> & {
  physicians?: HospitalPhysician[] | null
}

/**
 * 대표원장 이름이 `"A · B"`처럼 합쳐져 내려올 수 있다 — 합성 경로에서 그대로 쓰면
 * 한 사람의 이름이 두 사람 이름이 된다. 구분자로 나눠 각각 한 명으로 만든다.
 */
function splitDirectorNames(directorName: string): string[] {
  return directorName
    .split(/\s*[·,/]\s*/)
    .map((name) => name.trim())
    .filter(Boolean)
}

export function resolveClinicPhysicians(hospital: PhysicianSource): ClinicPhysician[] {
  const listed = (hospital.physicians ?? []).filter((physician) => physician.name?.trim())
  if (listed.length > 0) {
    // 마이그레이션으로 만들어진 행은 사진이 아직 연결돼 있지 않다. 대표가 한 명뿐이면
    // 종전처럼 검증된 원장 사진을 그 사람에게 붙여 배포 직후 초상이 사라지지 않게 한다.
    // 대표가 여럿이면 어느 사람의 사진인지 단정할 수 없으므로 연결된 사진만 쓴다.
    const representatives = listed.filter((physician) => physician.is_representative)
    // 다른 의료진에게 연결된 사진은 그 사람의 얼굴이므로 대표에게 붙이지 않는다.
    const legacyPhoto =
      representatives.length === 1
        ? selectUnlinkedVerifiedDoctorImage(
            hospital.photos ?? [],
            listed.filter((physician) => !physician.is_representative),
          )
        : null
    return listed.map((physician) => ({
      id: physician.id,
      name: physician.name.trim(),
      title: physician.title?.trim() || null,
      specialties: (physician.specialties ?? []).map((value) => value.trim()).filter(Boolean),
      career: physician.career?.trim() || null,
      credentials: physician.credentials ?? null,
      photoUrl:
        physician.photo_url ?? (physician.is_representative ? legacyPhoto : null),
      isRepresentative: physician.is_representative,
    }))
  }

  // 구버전 응답 — director_* 한 벌에서 합성한다. 검증된 원장 사진은 첫 번째(대표)에게만
  // 붙인다. 어느 사람의 사진인지 단정할 수 없는 자산을 여러 카드에 복제하지 않는다.
  const names = splitDirectorNames(hospital.director_name || '')
  if (names.length === 0) return []
  const verifiedPhoto = selectVerifiedDoctorImage(hospital.photos ?? [])
  return names.map((name, index) => ({
    id: `director-${index}`,
    name,
    title: null,
    specialties: index === 0 ? hospital.specialties ?? [] : [],
    career: index === 0 ? hospital.director_career?.trim() || null : null,
    credentials: index === 0 ? hospital.director_credentials ?? null : null,
    photoUrl: index === 0 && names.length === 1 ? verifiedPhoto : null,
    isRepresentative: true,
  }))
}

export function clinicPhysicianLayout(count: number): ClinicPhysicianLayout {
  if (count <= 1) return 'solo'
  if (count <= 3) return 'pair'
  return 'grid'
}

/**
 * 이름 위 라벨 — 운영자가 적은 그 사람의 직함(대표원장·부원장…)을 모든 의료진에게 그대로
 * 쓴다. 직함이 비면 아무것도 적지 않는다. 대표 표시는 대표 사진·순서만 정하고 라벨 문구를
 * 만들지 않는다 — 직함이 빈 대표에게 `대표원장`을 지어 붙이던 문제(2026-10-14 노원탑365의원).
 */
export function physicianEyebrow(physician: ClinicPhysician): string | null {
  return physician.title?.trim() || null
}

/**
 * 이름 아래 역할 라벨. 직함은 이미 이름 위에 있으므로, 직함이 없을 때만 승인된 전문의
 * 자격에서 역할을 가져온다(`대표원장` 같은 직함 문자열은 자격에서 고르지 않는다).
 */
export function physicianRoleLabel(physician: ClinicPhysician): string | null {
  if (physicianEyebrow(physician)) return null
  return selectDoctorRole(physician.credentials?.board_certifications ?? [])
}

/**
 * 이름 뒤 존칭. 직함이 있으면 그 직함이 유일한 역할 라벨이므로 붙이지 않는다.
 * 붙일 때는 앞에 **공백을 포함해** 돌려준다 — `전상훈원장`으로 붙어 읽히던 버그를
 * 마진이 아니라 텍스트로 막는다(답변 엔진은 마진을 보지 않는다).
 */
export function physicianNameSuffix(physician: ClinicPhysician): string | null {
  return physician.title ? null : '원장'
}

export function physicianDisplayChips(physician: ClinicPhysician, region: string[]): string[] {
  const values = [...physician.specialties, ...region].map((value) => value.trim()).filter(Boolean)
  return [...new Set(values)].slice(0, PHYSICIAN_CHIP_LIMIT)
}

export function physicianCareerNeedsDisclosure(career: string | null): boolean {
  return (career?.length ?? 0) > PHYSICIAN_CAREER_CLAMP
}

/** JSON-LD Physician 노드의 안정적인 `@id` 조각. */
export function physicianNodeId(
  hospitalRootUrl: string,
  physician: ClinicPhysician,
  index: number,
): string {
  const fragment = physician.id?.trim() || String(index)
  return `${hospitalRootUrl}/doctor#physician-${fragment}`
}
