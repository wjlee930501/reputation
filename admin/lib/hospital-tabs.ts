/**
 * 병원 화면 탭(결정 대기 2번 A, 11-24 회의 결정 전 draft).
 *
 * 병원 정보 한 화면(약 13,000px)을 병원 정보 · 공개 페이지 · 근거 자료 · 도메인·검색으로 나눠 탭이
 * 7개가 된다. 화면 조각(컴포넌트)은 `info/` 그대로 두고 새 탭이 가져다 쓴다 — 레이아웃 편집기
 * (`/info/layout`)와 그 진입 카드(SiteLayoutSection)는 옮기지 않는다.
 *
 * 옛 `/info#…` 앵커로 저장된 링크(Slack·사고 admin_path·편집기의 돌아가기 링크)는 병원 정보 화면이
 * `movedInfoAnchorTarget`으로 새 탭에 보낸다. 서버는 `#`을 볼 수 없으므로 브라우저에서 바꾼다.
 */

export interface HospitalTab {
  label: string
  /** `/hospitals/{id}/` 뒤 경로 조각. 현황은 빈 문자열이다. */
  path: string
  hint: string
}

export type MovedInfoTab = 'public-page' | 'sources' | 'domain'

/** 병원 정보에서 다른 탭으로 옮긴 구획의 앵커 → 새 탭. 앵커 이름은 그대로 둔다. */
export const MOVED_INFO_ANCHORS: Readonly<Record<string, MovedInfoTab>> = {
  // 공개 페이지: 레이아웃·페이지 문구·로고·색·첫 화면(BrandSection)과 사진·사용 권리.
  'info-brand': 'public-page',
  'info-image-style-direction': 'public-page',
  'info-photos': 'public-page',
  // 근거 자료: 원장 인터뷰 설문·문서·블로그·자료 목록.
  'info-sources': 'sources',
  // 도메인·검색: 자기 도메인 연결과 검색엔진 소유 확인.
  'domain-setup': 'domain',
  'search-ownership': 'domain',
  'naver-site-verification': 'domain',
  'daum-webmaster-verification': 'domain',
}

/**
 * `/info`에 붙어 온 앵커가 옮긴 구획을 가리키면 새 탭 주소를, 아니면 null.
 * query는 버린다 — 병원 정보 화면은 query를 읽지 않고, 새 탭도 마찬가지다.
 */
export function movedInfoAnchorTarget(hospitalId: string, hash: string): string | null {
  let anchor = hash.replace(/^#/, '')
  try {
    anchor = decodeURIComponent(anchor)
  } catch {
    return null
  }
  const tab = MOVED_INFO_ANCHORS[anchor]
  return tab ? `/hospitals/${hospitalId}/${tab}#${anchor}` : null
}
