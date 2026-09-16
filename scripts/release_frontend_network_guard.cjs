// Inherited by Node compiler children; no production/network API is reachable.
const net = require('node:net')
const original = net.Socket.prototype.connect
const local = new Set(['localhost', '127.0.0.1', '::1', '[::1]'])
net.Socket.prototype.connect = function (...args) {
  let first = args[0]
  if (Array.isArray(first)) first = first[0]
  const options = typeof first === 'object' && first !== null
    ? first
    : typeof first === 'number'
      ? { port: first, host: typeof args[1] === 'string' ? args[1] : 'localhost' }
      : { path: first }
  // A test-only logical host keeps production URL validation intact.
  // Resolve it here, without DNS or changes to the machine's hosts file.
  if (options.host === 'release-api.example.test') options.host = '127.0.0.1'
  if (!options.path && !local.has(options.host || 'localhost')) {
    throw new Error('Release frontend verification forbids non-loopback connections')
  }
  return original.apply(this, args)
}
