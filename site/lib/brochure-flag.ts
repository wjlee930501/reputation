/**
 * 홈의 소개서 진입 링크 노출 여부.
 *
 * `/brochure` 자체는 언제나 열려 있지만, 백엔드가 소개서 리드(`/brochure-leads`)를 받기
 * 전에 홈에서 사람을 보내면 열람은 되고 리드는 남지 않는다. 백엔드 배포 뒤
 * `NEXT_PUBLIC_BROCHURE_ENABLED=1`로 켠다. 빌드 시점 값이다.
 */
export const BROCHURE_ENABLED = process.env.NEXT_PUBLIC_BROCHURE_ENABLED === '1'
