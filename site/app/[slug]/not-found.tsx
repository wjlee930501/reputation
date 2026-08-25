import type { Metadata } from 'next'

import ClinicNotFound from './ClinicNotFound'

export const metadata: Metadata = {
  title: '병원 페이지를 찾을 수 없습니다',
  robots: { index: false, follow: false },
}

export default function HospitalNotFound() {
  return <ClinicNotFound />
}
