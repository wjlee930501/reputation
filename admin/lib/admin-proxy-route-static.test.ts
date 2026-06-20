import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const routeSource = readFileSync(join(process.cwd(), 'app/api/admin/[...path]/route.ts'), 'utf8')

test('admin API route delegates to the behavior-tested proxy handler', () => {
  assert.match(routeSource, /handleAdminApiProxy/)
  assert.match(routeSource, /export const GET = handleAdminApiProxy/)
  assert.match(routeSource, /export const POST = handleAdminApiProxy/)
})

test('admin API proxy applies no-store to direct BFF text errors', () => {
  const handlerSource = readFileSync(join(process.cwd(), 'lib/admin-api-proxy-route.ts'), 'utf8')

  assert.match(handlerSource, /function textNoStore/)
  assert.match(handlerSource, /textNoStore\('Method Not Allowed'/)
  assert.match(handlerSource, /textNoStore\('Forbidden'/)
  assert.doesNotMatch(handlerSource, /new NextResponse\('Forbidden'/)
  assert.doesNotMatch(handlerSource, /new NextResponse\('Method Not Allowed'/)
})
