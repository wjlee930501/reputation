export const DEFAULT_CNAME_TARGET = 'cname.reputation.motionlabs.kr'

// 플랫폼 기본 공개 호스트 (예: reputation.motionlabs.kr). 병원은 자기 도메인 없이도
// {slug}.{host} 로 노출된다(하이브리드 기본). NEXT_PUBLIC_SITE_URL이 있으면 그 host 사용.
const DEFAULT_SITE_HOST = 'reputation.motionlabs.kr'

export function platformSiteHost(): string {
  const raw = process.env.NEXT_PUBLIC_SITE_URL?.trim()
  if (raw) {
    try {
      return new URL(raw).hostname.toLowerCase()
    } catch {
      // 잘못된 SITE_URL은 무시 — 기본 호스트로 폴백.
    }
  }
  return DEFAULT_SITE_HOST
}

/** 병원 기본 공개 주소(자기 도메인 불필요). slug 없으면 null. */
export function platformSubdomainUrl(slug: string | null | undefined): string | null {
  const s = (slug ?? '').trim()
  if (!s) return null
  return `https://${s}.${platformSiteHost()}`
}

export function trimmed(value: string | null | undefined): string { return (value ?? '').trim() }

export function statusBadge(status: 'live' | 'waiting' | 'unsaved' | 'empty') {
  const badges = {
    live: { label: '연결 완료', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
    waiting: { label: '저장됨 · DNS 미확인', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
    unsaved: { label: '저장 필요', cls: 'bg-blue-50 text-blue-700 border-blue-200' },
    empty: { label: '미설정', cls: 'bg-slate-50 text-slate-600 border-slate-200' },
  } as const
  return badges[status]
}
