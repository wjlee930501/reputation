import assert from 'node:assert/strict'
import test from 'node:test'

import { ApiError } from './api.ts'
import { curatedReferenceRejectionMessage } from './content-save-errors.ts'

const URL = 'https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=3796'
const MESSAGE = `진료비·병원 선택 글에는 검증된 문서 목록의 질환 문서를 참고 자료로 넣을 수 없습니다. 넣을 수 없는 주소: ${URL}`

function rejection(status: number, detail: unknown): ApiError {
  return new ApiError('입력값 검증에 실패했습니다.', status, detail)
}

test('목록 문서 거절(422)은 서버 안내 문장과 넣을 수 없는 주소를 그대로 보여 준다', () => {
  const shown = curatedReferenceRejectionMessage(
    rejection(422, { code: 'CURATED_REFERENCE_NOT_ALLOWED', message: MESSAGE, urls: [URL] }),
  )
  assert.equal(shown, MESSAGE)
  assert.ok(shown?.includes(URL))
})

test('다른 저장 실패는 이 경로로 보이지 않는다(일반 안내로 바뀐다)', () => {
  const otherCode = rejection(422, { code: 'SOMETHING_ELSE', message: '내부 오류 문장' })
  const other400 = rejection(400, { code: 'CURATED_REFERENCE_NOT_ALLOWED', message: MESSAGE })
  const noMessage = rejection(422, { code: 'CURATED_REFERENCE_NOT_ALLOWED', urls: [URL] })
  const plain = new Error(MESSAGE)
  for (const error of [otherCode, other400, noMessage, plain, null]) {
    assert.equal(curatedReferenceRejectionMessage(error), null)
  }
})
