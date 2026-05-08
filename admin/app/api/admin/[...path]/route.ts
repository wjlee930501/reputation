import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";
const ADMIN_KEY = process.env.ADMIN_SECRET_KEY || "";

const ALLOWED_PREFIXES = ["hospitals", "content", "reports", "sov", "domain", "essence", "leads"];

async function handler(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path: pathSegments } = await params;
  const path = pathSegments.join("/");
  if (!ADMIN_KEY) {
    return NextResponse.json({ error: "Server misconfigured" }, { status: 500 });
  }
  const firstSegment = path.split("/")[0];
  if (!ALLOWED_PREFIXES.includes(firstSegment)) {
    return new NextResponse("Forbidden", { status: 403 });
  }
  const url = new URL(`/api/v1/admin/${path}`, BACKEND_URL);

  req.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.set(key, value);
  });

  const headers: Record<string, string> = {
    "X-Admin-Key": ADMIN_KEY,
  };

  const contentType = req.headers.get("content-type");
  if (contentType) {
    headers["content-type"] = contentType;
  }

  const fetchOptions: RequestInit = {
    method: req.method,
    headers,
  };

  if (req.method !== "GET" && req.method !== "HEAD") {
    // multipart/form-data 등 binary body를 보존하기 위해 arrayBuffer로 그대로 전달.
    // text() 사용 시 multipart boundary가 깨져 backend에서 파싱 실패.
    fetchOptions.body = await req.arrayBuffer();
  }

  const res = await fetch(url.toString(), fetchOptions);

  const responseBody = await res.text();
  return new NextResponse(responseBody, {
    status: res.status,
    headers: {
      "content-type": res.headers.get("content-type") || "application/json",
    },
  });
}

export const GET = handler;
export const POST = handler;
export const PATCH = handler;
export const PUT = handler;
export const DELETE = handler;
