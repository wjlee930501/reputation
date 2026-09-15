import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  clinicPhysicianLayout,
  physicianCareerNeedsDisclosure,
  physicianDisplayChips,
  physicianEyebrow,
  physicianNameSuffix,
  physicianNodeId,
  physicianRoleLabel,
  resolveClinicPhysicians,
  PHYSICIAN_CHIP_LIMIT,
  type ClinicPhysician,
} from './clinic-physicians.ts'
import type { HospitalPhysician } from './hospital-payload.ts'

const HERE = dirname(fileURLToPath(import.meta.url))

function row(overrides: Partial<HospitalPhysician> = {}): HospitalPhysician {
  return {
    id: 'p1',
    name: '김성열',
    title: null,
    specialties: [],
    career: null,
    credentials: null,
    photo_url: null,
    is_representative: false,
    display_order: 0,
    ...overrides,
  }
}

function physician(overrides: Partial<ClinicPhysician> = {}): ClinicPhysician {
  return {
    id: 'p1',
    name: '김성열',
    title: null,
    specialties: [],
    career: null,
    credentials: null,
    photoUrl: null,
    isRepresentative: false,
    ...overrides,
  }
}

const EMPTY_HOSPITAL = {
  director_name: '',
  director_career: '',
  director_credentials: null,
  specialties: [] as string[],
  photos: [],
}

test('the listed physicians win over the legacy director fields', () => {
  const resolved = resolveClinicPhysicians({
    ...EMPTY_HOSPITAL,
    director_name: '김성열 · 전상훈',
    physicians: [
      row({ id: 'a', name: ' 전상훈 ', title: ' 진료부장 ', specialties: [' 내과 ', ''] }),
      row({ id: 'b', name: '김성열', is_representative: true }),
    ],
  })

  assert.deepEqual(resolved.map((p) => p.id), ['a', 'b'])
  assert.equal(resolved[0].name, '전상훈')
  assert.equal(resolved[0].title, '진료부장')
  assert.deepEqual(resolved[0].specialties, ['내과'])
})

const VERIFIED_DOCTOR_PHOTO = {
  id: 'doctor-real',
  source_type: 'PHOTO_DOCTOR' as const,
  title: '원장 진료 사진',
  url: '/api/v1/public/clinic/assets/doctor-real',
  asset_kind: 'VERIFIED_REAL_PERSON' as const,
  approved_usage: ['DOCTOR_IDENTITY'],
}

test('a sole representative without a linked photo keeps the verified portrait', () => {
  // 마이그레이션이 만든 행은 photo_url이 비어 있다. 대표가 한 명이면 종전 규칙(검증된 원장
  // 사진)을 그 사람에게 붙여 배포 직후 초상이 사라지지 않게 한다.
  const resolved = resolveClinicPhysicians({
    ...EMPTY_HOSPITAL,
    photos: [VERIFIED_DOCTOR_PHOTO],
    physicians: [
      row({ id: 'a', name: '김성열', is_representative: true }),
      row({ id: 'b', name: '전상훈' }),
    ],
  })

  assert.equal(resolved[0].photoUrl, 'http://localhost:8000/api/v1/public/clinic/assets/doctor-real')
  assert.equal(resolved[1].photoUrl, null)
})

test('several representatives without linked photos get no portrait — never a guess', () => {
  const resolved = resolveClinicPhysicians({
    ...EMPTY_HOSPITAL,
    photos: [VERIFIED_DOCTOR_PHOTO],
    physicians: [
      row({ id: 'a', name: '김성열', is_representative: true }),
      row({ id: 'b', name: '전상훈', is_representative: true }),
    ],
  })

  assert.ok(resolved.every((p) => p.photoUrl === null))
})

test('a payload without physicians still renders the director, one card per name', () => {
  // 구버전 ISR 캐시 응답. `director_name`이 "A · B"로 합쳐져 있으면 한 사람의 이름이
  // 두 사람 이름이 되므로 나눠서 한 명씩 만든다.
  const resolved = resolveClinicPhysicians({
    ...EMPTY_HOSPITAL,
    director_name: '김성열 · 전상훈',
    director_career: '서울대학교 의과대학 졸업',
    specialties: ['대장항문외과'],
  })

  assert.deepEqual(resolved.map((p) => p.name), ['김성열', '전상훈'])
  assert.ok(resolved.every((p) => p.isRepresentative))
  // 어느 사람의 것인지 단정할 수 없는 약력·사진을 두 카드에 복제하지 않는다.
  assert.equal(resolved[0].career, '서울대학교 의과대학 졸업')
  assert.equal(resolved[1].career, null)
  assert.equal(resolved[0].photoUrl, null)
  assert.equal(resolved[1].photoUrl, null)
})

test('a hospital with no physician at all renders no card', () => {
  assert.deepEqual(resolveClinicPhysicians(EMPTY_HOSPITAL), [])
  assert.deepEqual(resolveClinicPhysicians({ ...EMPTY_HOSPITAL, physicians: [] }), [])
  // 이름이 공백뿐인 행은 카드가 되지 않는다.
  assert.deepEqual(
    resolveClinicPhysicians({ ...EMPTY_HOSPITAL, physicians: [row({ name: '  ' })] }),
    [],
  )
})

test('the card count picks the layout: solo, two-column, three-column', () => {
  assert.equal(clinicPhysicianLayout(0), 'solo')
  assert.equal(clinicPhysicianLayout(1), 'solo')
  assert.equal(clinicPhysicianLayout(2), 'pair')
  assert.equal(clinicPhysicianLayout(3), 'pair')
  assert.equal(clinicPhysicianLayout(4), 'grid')
  assert.equal(clinicPhysicianLayout(12), 'grid')
})

test('a title equal to the eyebrow is printed once, not twice', () => {
  // `협진 협진` 가드와 같은 실패 모드 — 라벨 두 곳이 같은 문자열을 받으면 중복된다.
  const representative = physician({ isRepresentative: true, title: '대표원장' })
  assert.equal(physicianEyebrow(representative), '대표원장')
  assert.equal(physicianRoleLabel(representative), null)

  // 서로 다른 값이면 둘 다 남는다.
  const withTitle = physician({ isRepresentative: true, title: '대장항문외과 전문의' })
  assert.equal(physicianRoleLabel(withTitle), '대장항문외과 전문의')

  // 직함이 없으면 승인된 전문의 자격에서 역할을 가져온다.
  const fromCredentials = physician({
    credentials: { board_certifications: ['대표원장', '외과 전문의'] },
  })
  assert.equal(physicianRoleLabel(fromCredentials), '외과 전문의')
  assert.equal(physicianRoleLabel(physician()), null)
})

test('the honorific carries its own space so the name never runs into it', () => {
  // `김성열 · 전상훈원장`으로 붙어 읽히던 버그. 마진은 화면만 고치고 추출된 문장은
  // 그대로 붙어 있어서 답변 엔진이 잘못된 이름을 인용한다.
  assert.equal(physicianNameSuffix(physician()), '원장')
  // 직함이 있으면 그 직함이 유일한 역할 라벨이다 — `원장`을 겹쳐 붙이지 않는다.
  assert.equal(physicianNameSuffix(physician({ title: '진료부장' })), null)

  const component = readFileSync(
    join(HERE, '..', 'app', '[slug]', '_components', 'DoctorIntro.tsx'),
    'utf8',
  )
  assert.match(component, /<small>\{` \$\{suffix\}`\}<\/small>/)
  assert.doesNotMatch(component, /<small>원장<\/small>/)
})

test('chips stay bounded and never repeat a value', () => {
  const chips = physicianDisplayChips(
    physician({ specialties: ['내과', '내과', '가정의학과', '건강검진', '소화기', '갑상선', '대사'] }),
    ['강남구'],
  )
  assert.equal(chips.length, PHYSICIAN_CHIP_LIMIT)
  assert.deepEqual(chips.slice(0, 2), ['내과', '가정의학과'])
})

test('a long career is folded behind a disclosure instead of flooding the card', () => {
  assert.equal(physicianCareerNeedsDisclosure(null), false)
  assert.equal(physicianCareerNeedsDisclosure('가'.repeat(280)), false)
  assert.equal(physicianCareerNeedsDisclosure('가'.repeat(281)), true)
})

test('each physician gets a stable node id, so the two pages describe the same person', () => {
  assert.equal(
    physicianNodeId('https://clinic.example', physician({ id: 'abc' }), 0),
    'https://clinic.example/doctor#physician-abc',
  )
  // id가 비어 있어도 노드가 충돌하지 않게 순번으로 물러난다.
  assert.equal(
    physicianNodeId('https://clinic.example', physician({ id: '' }), 2),
    'https://clinic.example/doctor#physician-2',
  )
})

test('the home and the doctor page emit one Physician node per physician', () => {
  const home = readFileSync(join(HERE, '..', 'app', '[slug]', 'page.tsx'), 'utf8')
  const doctor = readFileSync(join(HERE, '..', 'app', '[slug]', 'doctor', 'page.tsx'), 'utf8')

  // 병원 노드는 employee[]로 사람을 싣는다 — 한 명만 담던 merged `physician:`은 없다.
  assert.match(home, /employee: employeeJsonLd\.length > 0 \? employeeJsonLd : undefined/)
  assert.doesNotMatch(home, /'@id': `\$\{hospitalRootUrl\}\/doctor#physician`/)

  // /doctor는 병원을 인라인으로 복제하지 않고 허브가 소유한 @id를 참조한다.
  assert.match(doctor, /worksFor: \{ '@id': `\$\{hospitalRootUrl\}#clinic` \}/)
  assert.doesNotMatch(doctor, /worksFor: \{\s*\n\s*'@type': 'MedicalClinic'/)

  for (const source of [home, doctor]) {
    assert.match(source, /physicianNodeId\(hospitalRootUrl, physician, index\)/)
  }
})
