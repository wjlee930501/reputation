import { proxySurveyRequest } from '@/lib/doctor-survey-proxy'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

type Context = { params: Promise<{ token: string; action?: string[] }> }

export async function GET(request: Request, { params }: Context) {
  return proxySurveyRequest(request, params)
}

export async function POST(request: Request, { params }: Context) {
  return proxySurveyRequest(request, params)
}

export async function PUT(request: Request, { params }: Context) {
  return proxySurveyRequest(request, params)
}
