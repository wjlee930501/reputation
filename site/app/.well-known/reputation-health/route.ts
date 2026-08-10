import type { NextRequest } from 'next/server.js'

import { resolveReputationHealth } from '@/lib/reputation-health'

export async function GET(request: NextRequest): Promise<Response> {
  return resolveReputationHealth(request.headers)
}
