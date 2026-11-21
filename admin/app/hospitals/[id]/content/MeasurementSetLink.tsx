import Link from 'next/link'

import { ADMIN_COPY } from '@/lib/admin-copy'

const COPY = ADMIN_COPY.measurementSet

/** 콘텐츠 화면의 '측정 키워드 30개' 카드 — 편집은 하위 화면(/content/questions)에서 한다. */
export function MeasurementSetLink({ hospitalId }: { hospitalId: string }) {
  return (
    <section className="mt-5 flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white p-4">
      <div className="min-w-0 flex-1 space-y-1">
        <h3 className="text-sm font-semibold text-slate-800">{COPY.linkTitle}</h3>
        <p className="text-xs text-slate-500">{COPY.linkHint}</p>
      </div>
      <Link
        href={`/hospitals/${hospitalId}/content/questions`}
        className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
      >
        {COPY.linkAction}
      </Link>
    </section>
  )
}
