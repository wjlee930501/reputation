export {
  DEFAULT_CNAME_TARGET,
  platformSiteHost,
  platformSubdomainUrl,
} from '@/lib/platform-domain'

export function trimmed(value: string | null | undefined): string { return (value ?? '').trim() }

export function statusBadge(status: 'live' | 'waiting' | 'dns_verified' | 'issuing' | 'failed' | 'unsaved' | 'ready' | 'empty') {
  const badges = {
    live: { label: '연결 완료', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
    dns_verified: { label: 'DNS 확인 완료', cls: 'bg-emerald-50 text-emerald-600 border-emerald-200' },
    issuing: { label: 'DNS 확인 완료 · 인증서 발급 중', cls: 'bg-blue-100 text-blue-700 border-blue-200' },
    failed: { label: 'DNS 확인 완료 · 인증서 실패', cls: 'bg-amber-100 text-amber-700 border-amber-200' },
    waiting: { label: '저장됨 · DNS 미확인', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
    unsaved: { label: '저장 필요', cls: 'bg-blue-50 text-blue-700 border-blue-200' },
    ready: { label: '활성화 가능', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
    empty: { label: '미설정', cls: 'bg-slate-50 text-slate-600 border-slate-200' },
  } as const
  return badges[status]
}
