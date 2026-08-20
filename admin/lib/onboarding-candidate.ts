export interface ProfileUrlCandidate {
  key: string
  title: string
  sourceType: string
  url: string
}

export type CandidateAddResult = 'crawled' | 'already_in_profile'

type CandidateFetcher = (
  path: string,
  options: { method: 'POST'; body: string },
) => Promise<unknown>

const PROFILE_ONLY_CANDIDATE_KEYS = new Set([
  'naver_place_url',
  'google_business_profile_url',
  'google_maps_url',
  'kakao_channel_url',
])

export function isProfileOnlyCandidate(candidateKey: string): boolean {
  return PROFILE_ONLY_CANDIDATE_KEYS.has(candidateKey)
}

export async function addProfileUrlCandidate(
  fetcher: CandidateFetcher,
  hospitalId: string,
  candidate: ProfileUrlCandidate,
): Promise<CandidateAddResult> {
  if (isProfileOnlyCandidate(candidate.key)) return 'already_in_profile'

  await fetcher(`/admin/hospitals/${hospitalId}/essence/sources/crawl`, {
    method: 'POST',
    body: JSON.stringify({
      source_type: candidate.sourceType,
      title: candidate.title,
      url: candidate.url,
    }),
  })
  return 'crawled'
}
