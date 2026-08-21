import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildPhotoProvenancePatch,
  isPhotoProvenanceVerified,
  type PhotoProvenanceDraft,
  type PhotoProvenanceSource,
} from './photo-provenance.ts'

const legacyPhoto: PhotoProvenanceSource = {
  source_type: 'PHOTO_DOCTOR',
  source_metadata: {},
  photo_source_owner: null,
  photo_rights_basis: null,
  photo_evidence_reference: null,
  photo_verified_by: null,
  photo_verified_at: null,
}

test('legacy photos remain unverified without operator-entered provenance', () => {
  assert.equal(isPhotoProvenanceVerified(legacyPhoto), false)
})

test('complete operator provenance builds one classification patch without publication', () => {
  const draft: PhotoProvenanceDraft = {
    assetKind: 'VERIFIED_REAL_PERSON',
    sourceOwner: '홍길동 원장',
    rightsBasis: 'OWNER_CONSENT',
    evidenceReference: 'consent/doctor-42',
  }

  assert.deepEqual(buildPhotoProvenancePatch(legacyPhoto, draft), {
    source_metadata: {
      asset_kind: 'VERIFIED_REAL_PERSON',
      approved_usage: ['DOCTOR_IDENTITY'],
    },
    photo_source_owner: '홍길동 원장',
    photo_rights_basis: 'OWNER_CONSENT',
    photo_evidence_reference: 'consent/doctor-42',
  })
})

test('reapproval never invents missing evidence or a rights basis', () => {
  const incomplete: PhotoProvenanceDraft = {
    assetKind: 'VERIFIED_REAL_PERSON',
    sourceOwner: '홍길동 원장',
    rightsBasis: '',
    evidenceReference: '',
  }

  assert.equal(buildPhotoProvenancePatch(legacyPhoto, incomplete), null)
})

test('server verifier and exact semantic usage are required before publication', () => {
  const verified: PhotoProvenanceSource = {
    ...legacyPhoto,
    source_metadata: {
      asset_kind: 'VERIFIED_REAL_PERSON',
      approved_usage: ['DOCTOR_IDENTITY'],
    },
    photo_source_owner: '홍길동 원장',
    photo_rights_basis: 'OWNER_CONSENT',
    photo_evidence_reference: 'consent/doctor-42',
    photo_verified_by: 'owner@example.com',
    photo_verified_at: '2026-08-22T12:00:00Z',
  }

  assert.equal(isPhotoProvenanceVerified(verified), true)
  assert.equal(
    isPhotoProvenanceVerified({ ...verified, photo_verified_at: null }),
    false,
  )
})
