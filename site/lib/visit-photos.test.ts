import assert from 'node:assert/strict'
import test from 'node:test'

import type { HospitalPhoto } from './hospital-payload.ts'
import { selectVisitFacilityPhotos } from './visit-photos.ts'

const photo = (id: string, source_type: HospitalPhoto['source_type']): HospitalPhoto => ({
  id,
  source_type,
  title: id,
  url: `https://cdn.example.com/${id}.jpg`,
})

test('visit facility photos are empty when the hospital has no photos', () => {
  assert.deepEqual(selectVisitFacilityPhotos([]), [])
})

test('visit facility photos exclude a doctor-only collection', () => {
  assert.deepEqual(selectVisitFacilityPhotos([photo('doctor', 'PHOTO_DOCTOR')]), [])
})

test('visit facility photos keep all three supported facility types', () => {
  const facilities = [
    photo('exterior', 'PHOTO_CLINIC_EXTERIOR'),
    photo('interior', 'PHOTO_CLINIC_INTERIOR'),
    photo('treatment', 'PHOTO_TREATMENT_ROOM'),
  ]

  assert.deepEqual(selectVisitFacilityPhotos(facilities), facilities)
})

test('visit facility photos drop doctor photos from a mixed collection', () => {
  assert.deepEqual(
    selectVisitFacilityPhotos([
      photo('doctor', 'PHOTO_DOCTOR'),
      photo('interior', 'PHOTO_CLINIC_INTERIOR'),
    ]).map(({ id }) => id),
    ['interior'],
  )
})
