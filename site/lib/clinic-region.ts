/**
 * 병원 지역을 문장 안에 넣을 때 쓰는 짧은 이름.
 *
 * 병원 지역(`region`)은 마케터가 노출을 넓히려고 생활권 태그를 여러 개(많게는 13개) 넣는 칸이다.
 * 그대로 띄어쓰기로 이어 문장에 넣으면 '제주특별자치도 서귀포시 법환동 서귀포 제주 서귀포 신시가지
 * 토평동 …에서 정형외과 진료를 맡고 있는'처럼 읽힌다(2026-11-17 마케팅 요청). 문장·한 줄 표기에는
 * 앞의 대표 지역만 쓰고, 태그 전체는 목록(` · `)·구조화 데이터·llms.txt에만 둔다.
 *
 * 앞에서부터 두 개(마케터가 맨 앞에 두는 행정 지역 + 동네)를 쓰되, 광역 이름은 줄여 쓰고
 * ('제주특별자치도' → '제주') 같은 지역을 두 번 말하는 태그('세종특별자치시' 뒤 '세종시')는 건너뛴다.
 */

const PROVINCE_SHORT: Record<string, string> = {
  서울특별시: '서울',
  부산광역시: '부산',
  대구광역시: '대구',
  인천광역시: '인천',
  광주광역시: '광주',
  대전광역시: '대전',
  울산광역시: '울산',
  세종특별자치시: '세종',
  경기도: '경기',
  강원도: '강원',
  강원특별자치도: '강원',
  충청북도: '충북',
  충청남도: '충남',
  전라북도: '전북',
  전북특별자치도: '전북',
  전라남도: '전남',
  경상북도: '경북',
  경상남도: '경남',
  제주특별자치도: '제주',
}

function shortenLabel(label: string): string {
  return label
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => PROVINCE_SHORT[word] ?? word)
    .join(' ')
}

/** '세종'과 '세종시', '경산'과 '경산시'를 같은 지역으로 본다. */
function placeRoot(word: string): string {
  return word.length > 2 ? word.replace(/시$/, '') : word
}

export function regionSentenceLabel(region: readonly string[] | null | undefined, limit = 2): string {
  const picked: string[] = []
  const seenRoots = new Set<string>()
  for (const raw of region ?? []) {
    const label = shortenLabel(raw.trim())
    if (!label) continue
    const roots = label.split(' ').map(placeRoot)
    if (roots.every((root) => seenRoots.has(root))) continue
    roots.forEach((root) => seenRoots.add(root))
    picked.push(label)
    if (picked.length >= limit) break
  }
  return picked.join(' ')
}
