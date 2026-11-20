import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

// 2026-11-20 마케팅 요청·결정 N1: 글 모음의 이름은 '의료 정보' 하나다(메뉴·경로·페이지 제목·구조화
// 정보가 같은 말). '건강 정보'는 글 유형 하나(HEALTH)의 이름으로만 남는다.
test('clinic pages call the article library "의료 정보", not "건강 정보"', () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'app', '[slug]')
  const offenders: string[] = []
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name)
      if (statSync(path).isDirectory()) walk(path)
      else if (/\.tsx?$/.test(name)) {
        const text = readFileSync(path, 'utf8').replace(/일반 건강 정보/g, '')
        if (text.includes('건강 정보')) offenders.push(path)
      }
    }
  }
  walk(root)
  assert.deepEqual(offenders, [])
})
