import { redirect } from 'next/navigation'

import { legacyHospitalRedirect } from '@/lib/route-redirects'

/** 옛 주소는 새 탭으로 보낸다(북마크·Slack 링크 보호). */
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const { id } = await params
  redirect(legacyHospitalRedirect(id, 'dashboard', await searchParams))
}
