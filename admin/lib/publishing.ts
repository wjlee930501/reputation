export interface ManualPublishPayload {
  published_by: string
}

export function normalizePublisherName(value: string): string | null {
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

export function buildManualPublishPayload(value: string): ManualPublishPayload | null {
  const publishedBy = normalizePublisherName(value)
  if (!publishedBy) return null
  return { published_by: publishedBy }
}
