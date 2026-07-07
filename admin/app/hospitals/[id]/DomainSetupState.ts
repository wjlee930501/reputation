export {
  DEFAULT_CNAME_TARGET,
  platformSiteHost,
  platformSubdomainUrl,
} from '@/lib/platform-domain'

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
