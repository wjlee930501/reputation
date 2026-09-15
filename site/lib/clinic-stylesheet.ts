import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SITE_ROOT = join(HERE, '..')

/**
 * 병원 공개 표면 스타일 레이어. `app/[slug]/layout.tsx`가 이 순서로 import한다 —
 * 캐스케이드 순서가 곧 우선순위이므로 테스트도 같은 순서로 이어 붙여 읽는다.
 * (layout.tsx와 어긋나면 `clinic-visual-system.test.ts`가 잡는다.)
 */
export const CLINIC_STYLE_FILES = [
  'tokens.css',
  'base.css',
  'header.css',
  'hero.css',
  'treatments.css',
  'doctor.css',
  'principles.css',
  'content.css',
  'flow.css',
  'gallery.css',
  'facts.css',
  'footer.css',
] as const

export const CLINIC_STYLES_DIR = join(SITE_ROOT, 'app', '[slug]', '_styles')
export const CLINIC_LAYOUT_PATH = join(SITE_ROOT, 'app', '[slug]', 'layout.tsx')
export const GLOBALS_CSS_PATH = join(SITE_ROOT, 'app', 'globals.css')

export function readClinicStyles(): string {
  return CLINIC_STYLE_FILES.map((file) => readFileSync(join(CLINIC_STYLES_DIR, file), 'utf8')).join(
    '\n',
  )
}

/** 브라우저가 보는 순서 그대로: 루트 globals.css 뒤에 병원 레이어. */
export function readClinicCascade(): string {
  return `${readFileSync(GLOBALS_CSS_PATH, 'utf8')}\n${readClinicStyles()}`
}
