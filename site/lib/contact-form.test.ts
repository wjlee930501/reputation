import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const source = readFileSync(join(process.cwd(), 'app/_components/ContactForm.tsx'), 'utf8')

test('contact form renders the five contact fields then the three diagnosis fields, in order', () => {
  const fieldNames = [...source.matchAll(/name="(clinicName|clinicAddress|directorName|directorPhone|homepage|specialty|regionKeyword|coreKeywords|email|message)"/g)]
    .map((match) => match[1])

  assert.deepEqual(fieldNames, [
    'clinicName',
    'clinicAddress',
    'directorName',
    'directorPhone',
    'homepage',
    'specialty',
    'regionKeyword',
    'coreKeywords',
  ])
})

test('contact form preserves required copy, placeholders, consent, and CTA', () => {
  for (const copy of [
    '병원명',
    'placeholder="OOO정형외과"',
    '병원 주소',
    'placeholder="서울 강남구 테헤란로 00"',
    '원장님 성함',
    '원장님 연락처',
    'placeholder="010-0000-0000"',
    '병원 홈페이지',
    '진료과',
    'placeholder="정형외과"',
    '지역 키워드',
    'placeholder="대구 OO동, 서울 OO동"',
    '핵심 진료 항목',
    'placeholder="건강검진, 신경차단술, 내성발톱"',
    '병원명은 넣지 마세요.',
    '개인정보 수집·이용에 동의합니다.',
    "body.set('consent_version', 'v1.2026-08')",
    '도입 문의하기',
  ]) {
    assert.ok(source.includes(copy), `missing contact form copy: ${copy}`)
  }
})

test('contact form derives source_path from pathname via inquirySourcePath', () => {
  assert.ok(source.includes("from 'next/navigation'"), 'missing usePathname import')
  assert.ok(source.includes('usePathname'), 'missing usePathname()')
  assert.ok(source.includes('inquirySourcePath'), 'missing inquirySourcePath')
  assert.ok(
    source.includes(
      "body.set('source_path', decorateSourcePath(inquirySourcePath(pathname), attribution.current))",
    ),
    'source_path should come from inquirySourcePath(pathname), decorated with the ad capture',
  )
  assert.ok(
    !source.includes("body.set('source_path', '/#contact')"),
    'source_path must not be hardcoded to /#contact',
  )
})

test('contact form keeps a real honeypot field and submits what a bot typed into it', () => {
  // 이 폼의 제출은 유료 초도 진단과 안내 문자를 일으킨다. honeypot을 상수 ''로 보내면
  // 서버 쪽 함정(leads-route.ts·backend public/leads.py)이 영원히 걸리지 않는다.
  assert.match(source, /name="website"/)
  assert.match(source, /body\.set\('website', honeypot\.current\?\.value \?\? ''\)/)
  assert.doesNotMatch(source, /body\.set\('website', ''\)/)
})
