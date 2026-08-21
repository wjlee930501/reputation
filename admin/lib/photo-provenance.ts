import type { SourceType } from '../types/index.ts'

export type PhotoProvenanceSource = {
  readonly source_type: SourceType
  readonly source_metadata: Readonly<Record<string, unknown>>
  readonly photo_source_owner: string | null
  readonly photo_rights_basis: string | null
  readonly photo_evidence_reference: string | null
  readonly photo_verified_by: string | null
  readonly photo_verified_at: string | null
}

export type PhotoProvenanceDraft = {
  readonly assetKind: string
  readonly sourceOwner: string
  readonly rightsBasis: string
  readonly evidenceReference: string
}

type PhotoProvenancePatch = {
  readonly source_metadata: Readonly<Record<string, unknown>>
  readonly photo_source_owner: string
  readonly photo_rights_basis: 'LICENSE' | 'OWNER_CONSENT'
  readonly photo_evidence_reference: string
}

function approvedUsage(sourceType: SourceType, assetKind: string): readonly string[] | null {
  if (sourceType === 'PHOTO_DOCTOR') {
    if (assetKind === 'VERIFIED_REAL_PERSON') return ['DOCTOR_IDENTITY']
    if (assetKind === 'EDITORIAL_GRAPHIC') return ['CONTENT_EDITORIAL']
    return null
  }
  if (sourceType === 'PHOTO_BRAND') {
    return assetKind === 'VERIFIED_BRAND_GRAPHIC' ? ['LOGO', 'HERO'] : null
  }
  if (
    sourceType === 'PHOTO_CLINIC_EXTERIOR'
    || sourceType === 'PHOTO_CLINIC_INTERIOR'
    || sourceType === 'PHOTO_TREATMENT_ROOM'
  ) {
    if (assetKind === 'VERIFIED_FACILITY') return ['HERO', 'GALLERY']
    if (assetKind === 'EDITORIAL_GRAPHIC') return ['CONTENT_EDITORIAL']
  }
  return null
}

export function buildPhotoProvenancePatch(
  source: PhotoProvenanceSource,
  draft: PhotoProvenanceDraft,
): PhotoProvenancePatch | null {
  const sourceOwner = draft.sourceOwner.trim()
  const evidenceReference = draft.evidenceReference.trim()
  const usage = approvedUsage(source.source_type, draft.assetKind)
  const rightsBasis = draft.rightsBasis === 'LICENSE' || draft.rightsBasis === 'OWNER_CONSENT'
    ? draft.rightsBasis
    : null
  if (!sourceOwner || !evidenceReference || !usage || !rightsBasis) return null
  return {
    source_metadata: {
      ...source.source_metadata,
      asset_kind: draft.assetKind,
      approved_usage: usage,
    },
    photo_source_owner: sourceOwner,
    photo_rights_basis: rightsBasis,
    photo_evidence_reference: evidenceReference,
  }
}

export function isPhotoProvenanceVerified(source: PhotoProvenanceSource): boolean {
  if (
    !source.photo_source_owner
    || !source.photo_evidence_reference
    || !source.photo_verified_by
    || !source.photo_verified_at
    || !['LICENSE', 'OWNER_CONSENT'].includes(source.photo_rights_basis ?? '')
  ) return false
  const assetKind = typeof source.source_metadata.asset_kind === 'string'
    ? source.source_metadata.asset_kind
    : ''
  const expectedUsage = approvedUsage(source.source_type, assetKind)
  const actualUsage = source.source_metadata.approved_usage
  return Boolean(
    expectedUsage
    && Array.isArray(actualUsage)
    && actualUsage.length === expectedUsage.length
    && expectedUsage.every((usage, index) => actualUsage[index] === usage),
  )
}
