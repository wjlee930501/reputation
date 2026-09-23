export const runtime = 'nodejs'
// 열람 쿠키·공유 토큰마다 응답이 달라진다. 정적으로 굳으면 게이트가 사라진다.
export const dynamic = 'force-dynamic'

export { getBrochureDocument as GET } from '../../../lib/brochure-routes.ts'
