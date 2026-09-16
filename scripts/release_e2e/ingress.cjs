// Fixed-target browser ingress only. App/DB/worker containers retain an internal-only network.
const http = require('node:http')
for (const [port, host, upstreamPort] of [[8080, 'qa-admin', 8080], [8081, 'qa-site', 8080], [8082, 'qa-api', 8000]]) {
  http.createServer((request, response) => {
    const upstream = http.request({ hostname: host, port: upstreamPort,
      path: request.url, method: request.method, headers: request.headers }, result => {
      response.writeHead(result.statusCode, result.headers)
      result.pipe(response)
    })
    upstream.on('error', () => { response.writeHead(502); response.end('Isolated upstream unavailable') })
    request.pipe(upstream)
  }).listen(port, '0.0.0.0')
}
