// 결과적으로 맞춰지는 상태를 기한 안에서 다시 읽는다. 맞으면 그 값, 기한이 지나면 마지막 관측을 돌려준다.
export async function pollUntil(read, isSettled, {
  timeoutMs,
  intervalMs = 500,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  clock = Date.now,
}) {
  const deadline = clock() + timeoutMs
  let value = await read()
  while (!isSettled(value) && clock() < deadline) {
    await sleep(intervalMs)
    value = await read()
  }
  return value
}
