function remotePatternFromEnv(value) {
  if (!value) return null
  try {
    const parsed = new URL(value)
    return {
      protocol: parsed.protocol.replace(':', ''),
      hostname: parsed.hostname,
      port: parsed.port,
    }
  } catch {
    return null
  }
}

const backendImageHosts = [
  'http://localhost:8000',
  'http://127.0.0.1:8000',
  process.env.NEXT_PUBLIC_BACKEND_URL,
  process.env.NEXT_PUBLIC_API_URL,
  process.env.BACKEND_URL,
]
  .map(remotePatternFromEnv)
  .filter(Boolean)

/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'storage.googleapis.com' },
      { protocol: 'https', hostname: '*.storage.googleapis.com' },
      ...backendImageHosts,
    ],
  },
}

export default nextConfig
