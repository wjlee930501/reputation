// 일괄 발행 청크 유틸.
// 완전 직렬 발행은 선택 건수가 많을수록 대기 시간이 선형으로 늘어난다.
// 3~5개 단위로 나눠 청크 내부는 Promise.allSettled로 동시 처리하고, 청크 사이만
// 순차 진행해 진행률 표시와 부분 실패 목록(실패한 항목만 재선택)을 그대로 지원한다.

export const BULK_PUBLISH_CHUNK_SIZE = 4

export function chunkIds<T>(ids: T[], size: number = BULK_PUBLISH_CHUNK_SIZE): T[][] {
  if (size <= 0) throw new Error('chunk size must be positive')
  const chunks: T[][] = []
  for (let i = 0; i < ids.length; i += size) {
    chunks.push(ids.slice(i, i + size))
  }
  return chunks
}

export interface BulkPublishResult {
  succeededIds: string[]
  failedIds: string[]
  firstFailureMessage: string | null
}

/**
 * ids를 청크 단위로 병렬 발행한다. publishOne이 던지는 오류는 그대로 실패로 집계하고
 * 같은 청크의 나머지 항목이나 다음 청크 처리를 막지 않는다.
 */
export async function runChunkedBulkPublish(
  ids: string[],
  publishOne: (id: string) => Promise<void>,
  options?: { chunkSize?: number; onProgress?: (done: number, total: number) => void },
): Promise<BulkPublishResult> {
  const total = ids.length
  const succeededIds: string[] = []
  const failedIds: string[] = []
  let firstFailureMessage: string | null = null
  let done = 0

  for (const chunk of chunkIds(ids, options?.chunkSize ?? BULK_PUBLISH_CHUNK_SIZE)) {
    const results = await Promise.allSettled(chunk.map((id) => publishOne(id)))
    results.forEach((result, idx) => {
      const id = chunk[idx]
      if (result.status === 'fulfilled') {
        succeededIds.push(id)
      } else {
        failedIds.push(id)
        if (!firstFailureMessage) {
          firstFailureMessage = result.reason instanceof Error ? result.reason.message : String(result.reason)
        }
      }
      done++
    })
    options?.onProgress?.(done, total)
  }

  return { succeededIds, failedIds, firstFailureMessage }
}
