import assert from 'node:assert/strict'
import test from 'node:test'

import { ApiError } from './api.ts'
import { profileSaveErrorMessage } from './profile-save-error.ts'

test('profile save shows the backend message and code for a rejected new external logo', () => {
  const message = profileSaveErrorMessage(new ApiError(
    '외부 사이트의 로고 주소는 공개 화면에 쓸 수 없습니다.',
    400,
    {
      code: 'EXTERNAL_LOGO_URL',
      message: '외부 사이트의 로고 주소는 공개 화면에 쓸 수 없습니다.',
    },
  ))

  assert.match(message, /외부 사이트의 로고 주소는 공개 화면에 쓸 수 없습니다/)
  assert.match(message, /오류 코드: EXTERNAL_LOGO_URL/)
  assert.doesNotMatch(message, /병원 온보딩 자료와 현재 진행 상태/)
})

test('profile save names structured missing fields instead of masking the mismatch', () => {
  const message = profileSaveErrorMessage(new ApiError(
    '프로파일 완료 상태에서는 필수 항목을 비울 수 없습니다.',
    422,
    {
      code: 'PROFILE_REQUIRED_FIELDS_MISSING',
      message: '프로파일 완료 상태에서는 필수 항목을 비울 수 없습니다.',
      missing_fields: ['director_name', { field: 'phone' }],
    },
  ))

  assert.match(message, /오류 코드: PROFILE_REQUIRED_FIELDS_MISSING/)
  assert.match(message, /확인할 항목: director_name, phone/)
})

test('profile save preserves a string backend detail', () => {
  assert.equal(
    profileSaveErrorMessage(new ApiError('전문과목 값을 확인해 주세요.', 422, '전문과목 값을 확인해 주세요.')),
    '전문과목 값을 확인해 주세요.',
  )
})
