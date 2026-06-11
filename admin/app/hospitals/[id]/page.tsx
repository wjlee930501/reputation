import { redirect } from 'next/navigation'

export default async function HospitalIndexPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = await params
  redirect(`/hospitals/${id}/dashboard`)
}
