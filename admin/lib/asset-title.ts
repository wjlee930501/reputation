// 업로드한 파일마다 서로 다른 제목을 갖게 하는 기본값.
//
// 여러 장을 한 번에 올릴 때 제목 칸이 하나뿐이면 N개 자산이 모두 같은 제목으로
// 저장되고, 그 제목이 공개 표면의 캡션·대체 텍스트가 되어 갤러리 전체가 같은
// 문구를 반복한다(D-1). 파일명은 서로 다르므로 좋은 출발점이다.
//
// 규칙은 백엔드 `_filename_without_extension`과 같게 유지한다.

const MAX_TITLE_LENGTH = 300

/** 경로와 확장자를 떼어낸 파일명. 비어 있으면 빈 문자열(서버가 기본값을 채운다). */
export function defaultAssetTitle(filename: string): string {
  if (!filename) return ''
  const basename = filename.replace(/\\/g, '/').split('/').pop() ?? ''
  // 백엔드와 같이 마지막 점 이전까지만 남긴다. 점으로 시작하는 이름은 빈 문자열이
  // 되고, 서버가 그때 기본 제목을 채운다.
  const lastDot = basename.lastIndexOf('.')
  const withoutExtension = lastDot >= 0 ? basename.slice(0, lastDot) : basename
  return withoutExtension.slice(0, MAX_TITLE_LENGTH)
}

/** 파일 목록에 맞춰 파일별 기본 제목을 만든다. */
export function defaultAssetTitles(filenames: readonly string[]): string[] {
  return filenames.map((filename) => defaultAssetTitle(filename))
}

/**
 * 같은 제목으로 저장된 자산들. 공개 표면에서 캡션이 똑같아지는 자산을 짚어낸다.
 *
 * 대소문자와 앞뒤 공백만 다른 제목도 화면에서는 같은 캡션으로 읽히므로 함께 묶는다.
 */
export function duplicateAssetTitles(titles: readonly string[]): string[] {
  const counts = new Map<string, { display: string; count: number }>()
  for (const title of titles) {
    const key = title.trim().toLowerCase()
    if (!key) continue
    const entry = counts.get(key)
    if (entry) entry.count += 1
    else counts.set(key, { display: title.trim(), count: 1 })
  }
  return [...counts.values()].filter((entry) => entry.count > 1).map((entry) => entry.display)
}
