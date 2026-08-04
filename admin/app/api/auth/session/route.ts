import { handleAdminSessionRead } from '../../../../lib/session-route.ts'

export const runtime = 'nodejs'

export const GET = handleAdminSessionRead
