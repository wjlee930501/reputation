import assert from 'node:assert/strict'
import http from 'node:http'
import { adminServiceOrigin, probeAdminUnavailable } from './admin_availability.mjs'

const configuredService = 'http://admin-new:3001'
assert.equal(adminServiceOrigin(configuredService), configuredService)
assert.notEqual(adminServiceOrigin(configuredService), 'http://localhost:3901')

const server = http.createServer((_request, response) => {
  response.writeHead(502, { 'content-type': 'text/plain' })
  response.end('synthetic proxy failure')
})
await new Promise((resolve, reject) => {
  server.once('error', reject)
  server.listen(0, '127.0.0.1', resolve)
})
const address = server.address()
assert(address && typeof address !== 'string')
const reachable502 = `http://127.0.0.1:${address.port}`
assert.equal(await probeAdminUnavailable(reachable502), false)
await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
assert.equal(await probeAdminUnavailable(reachable502), true)

console.log(JSON.stringify({
  status: 'passed',
  configuredService,
  localhostProxyExcluded: true,
  http502CountsAsReachable: true,
  rejectedConnectionCountsAsUnavailable: true,
}))
