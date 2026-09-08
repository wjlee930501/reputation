export function normalizePublisherName(value: string): string | null {
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

export function resolveAuditActorName(value: string | null | undefined): string | null {
  return normalizePublisherName(value ?? '')
}
