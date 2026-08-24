import assert from 'node:assert/strict'
import test from 'node:test'

import { profilePatchPayload } from './profile-patch.ts'

test('profile PATCH omits the upload-owned logo reference', () => {
  const payload = profilePatchPayload({
    name: '테스트의원',
    logo_url: 'gs://reputation-images/assets/hospital/logo.png',
    specialties: ['정형외과'],
  })

  assert.deepEqual(payload, {
    name: '테스트의원',
    specialties: ['정형외과'],
  })
  assert.equal('logo_url' in payload, false)
})
