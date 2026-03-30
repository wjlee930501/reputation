import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";
const ADMIN_KEY = process.env.ADMIN_SECRET_KEY || "";

const ALLOWED_PREFIXES = ["hospitals", "content", "reports", "sov", "domain"];

async function handler(
  req: NextRequest,
  { params }: { params: { path: string[] } }
) {
  const path = params.path.join("/");
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
    fetchOptions.body = await req.text();
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
