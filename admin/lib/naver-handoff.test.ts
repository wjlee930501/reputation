import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildNaverDeveloperContext,
  isNaverEvidenceAvailable,
  naverItemCopy,
  parseNaverOpenFailures,
  parseNaverHandoffResponse,
} from './naver-handoff.ts'

test('네이버 수집 결과를 한글 운영 상태로 해석한다', () => {
  const result = parseNaverHandoffResponse({
    operation_run_id: 'run-1',
    created: 1,
    skipped_duplicate: 0,
    skipped_empty: 0,
    failed: [],
    items: [
      {
        url: 'https://m.blog.naver.com/clinic/1',
        url_hash: 'hash-1',
        state: 'FAILED',
        safe_error_code: 'NAVER_HTTP_ERROR',
        safe_error_message: '네이버 블로그 글을 가져오지 못했습니다.',
        next_action: '실패한 글만 다시 수집해 주세요.',
        source_id: null,
        retry_of_run_id: null,
      },
    ],
  })

  const item = result.items[0]
  assert.ok(item)
  assert.equal(item.runId, 'run-1')
  assert.deepEqual(naverItemCopy(item), {
    label: '수집하지 못함',
    impact: '이 글은 아직 근거 자료에 추가되지 않았습니다. 다른 자료에는 영향이 없습니다.',
    action: '다시 수집을 눌러 주세요. 계속 실패하면 아래 정보를 복사해 개발팀에 문의해 주세요.',
    tone: 'danger',
  })
})

test('개발팀 문의 정보에는 원문이나 오류 원문이 포함되지 않는다', () => {
  const context = buildNaverDeveloperContext({
    hospitalId: 'hospital-1',
    runId: 'run-1',
    urlHash: 'hash-1',
    state: 'FAILED',
  })

  assert.match(context, /작업 번호: run-1/)
  assert.match(context, /글 식별값: hash-1/)
  assert.doesNotMatch(context, /https:/)
})

test('알 수 없는 상태를 성공처럼 표시하지 않는다', () => {
  assert.throws(
    () =>
      parseNaverHandoffResponse({
        operation_run_id: 'run-1',
        created: 0,
        skipped_duplicate: 0,
        skipped_empty: 0,
        failed: [],
        items: [{ url: 'x', url_hash: 'h', state: 'QUEUED' }],
      }),
    /네이버 수집 결과/,
  )
})

test('새로고침 뒤에도 조치가 필요한 실패 글을 복구 목록으로 읽는다', () => {
  const items = parseNaverOpenFailures({
    items: [{
      operation_run_id: 'run-1',
      url: 'https://m.blog.naver.com/clinic/1',
      url_hash: 'hash-1',
      state: 'FAILED',
      safe_error_code: 'NAVER_FETCH_FAILED',
      safe_error_message: '글을 가져오지 못했습니다.',
      next_action: '다시 수집해 주세요.',
      source_id: null,
      retry_of_run_id: null,
    }],
  })

  assert.equal(items.length, 1)
  assert.equal(items[0]?.runId, 'run-1')
  assert.equal(items[0]?.state, 'FAILED')
})

test('본문이 없는 재시도는 복구 성공으로 취급하지 않는다', () => {
  const base = {
    url: 'https://m.blog.naver.com/clinic/1',
    urlHash: 'hash-1',
    safeErrorMessage: null,
    nextAction: null,
    sourceId: null,
    retryOfRunId: null,
    runId: 'run-1',
  }

  assert.equal(isNaverEvidenceAvailable({ ...base, state: 'INGESTED', safeErrorCode: null }), true)
  assert.equal(isNaverEvidenceAvailable({ ...base, state: 'SKIPPED', safeErrorCode: 'DUPLICATE_SOURCE' }), true)
  assert.equal(isNaverEvidenceAvailable({ ...base, state: 'SKIPPED', safeErrorCode: 'EMPTY_CONTENT' }), false)
  assert.equal(isNaverEvidenceAvailable({ ...base, state: 'FAILED', safeErrorCode: 'NAVER_FETCH_FAILED' }), false)
})
