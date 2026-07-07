import assert from 'node:assert/strict'
import test from 'node:test'

import { chunkIds, runChunkedBulkPublish } from './bulk-publish.ts'

test('chunkIds splits into fixed-size groups with a smaller final chunk', () => {
  assert.deepEqual(chunkIds(['a', 'b', 'c', 'd', 'e'], 2), [['a', 'b'], ['c', 'd'], ['e']])
})

test('chunkIds returns a single chunk when size exceeds the list length', () => {
  assert.deepEqual(chunkIds(['a'], 4), [['a']])
})

test('chunkIds returns no chunks for an empty list', () => {
  assert.deepEqual(chunkIds([], 4), [])
})

test('runChunkedBulkPublish publishes all ids concurrently within a chunk', async () => {
  const calls: string[] = []
  const result = await runChunkedBulkPublish(['a', 'b', 'c'], async (id) => {
    calls.push(id)
  }, { chunkSize: 4 })

  assert.deepEqual(new Set(calls), new Set(['a', 'b', 'c']))
  assert.deepEqual(result.succeededIds, ['a', 'b', 'c'])
  assert.deepEqual(result.failedIds, [])
  assert.equal(result.firstFailureMessage, null)
})

test('runChunkedBulkPublish isolates a failure so the rest of the chunk still succeeds', async () => {
  const result = await runChunkedBulkPublish(['a', 'b', 'c'], async (id) => {
    if (id === 'b') throw new Error('금지 표현 발견')
  }, { chunkSize: 4 })

  assert.deepEqual(result.succeededIds, ['a', 'c'])
  assert.deepEqual(result.failedIds, ['b'])
  assert.equal(result.firstFailureMessage, '금지 표현 발견')
})

test('runChunkedBulkPublish processes later chunks even after an earlier chunk has failures', async () => {
  const order: string[] = []
  const result = await runChunkedBulkPublish(['a', 'b', 'c', 'd'], async (id) => {
    order.push(id)
    if (id === 'a') throw new Error('첫 청크 실패')
  }, { chunkSize: 2 })

  assert.deepEqual(order.sort(), ['a', 'b', 'c', 'd'])
  assert.deepEqual(result.succeededIds.sort(), ['b', 'c', 'd'])
  assert.deepEqual(result.failedIds, ['a'])
})

test('runChunkedBulkPublish reports cumulative progress across chunk boundaries', async () => {
  const progressCalls: Array<[number, number]> = []
  await runChunkedBulkPublish(['a', 'b', 'c', 'd', 'e'], async () => {}, {
    chunkSize: 2,
    onProgress: (done, total) => progressCalls.push([done, total]),
  })

  assert.deepEqual(progressCalls, [[2, 5], [4, 5], [5, 5]])
})
