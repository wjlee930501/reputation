import { NextRequest, NextResponse } from "next/server";

import { getBackendUrl } from "@/lib/backend";
import { buildProxyResponse } from "@/lib/proxy-response";
import { buildSafeAdminProxyPath, hasValidSameOrigin } from "@/lib/security";
import { readSessionToken } from "@/lib/session";

const ADMIN_KEY = process.env.ADMIN_SECRET_KEY || "";

const ALLOWED_PREFIXES = ["hospitals", "content", "reports", "sov", "domain", "essence", "leads"];

// Restrict forwarded verbs to the set the backend admin router actually supports.
const ALLOWED_METHODS = new Set(["GET", "POST", "PATCH", "DELETE"]);

async function handler(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  if (!ALLOWED_METHODS.has(req.method)) {
    return new NextResponse("Method Not Allowed", { status: 405 });
  }

  const { path: pathSegments } = await params;
  if (!ADMIN_KEY) {
    return NextResponse.json({ error: "Server misconfigured" }, { status: 500 });
  }
  const sessionSecret = process.env.ADMIN_SESSION_SECRET;
  if (!sessionSecret) {
    return NextResponse.json({ error: "Server misconfigured" }, { status: 500 });
  }

  if (!hasValidSameOrigin(req)) {
    return new NextResponse("Forbidden", { status: 403 });
  }

  const sessionToken = req.cookies.get("admin_session")?.value;
  const session = sessionToken ? await readSessionToken(sessionToken, sessionSecret) : null;
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const path = buildSafeAdminProxyPath(pathSegments, ALLOWED_PREFIXES);
  if (!path) {
    return new NextResponse("Forbidden", { status: 403 });
  }

  let backendUrl: string;
  try {
    backendUrl = getBackendUrl();
  } catch {
    return NextResponse.json({ error: "Server misconfigured" }, { status: 500 });
  }
  const url = new URL(`/api/v1/admin/${path}`, backendUrl);

  req.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.set(key, value);
  });

  const headers: Record<string, string> = {
    "X-Admin-Key": ADMIN_KEY,
    "X-Admin-Actor": session.email,
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
  return buildProxyResponse(res);
}

export const GET = handler;
export const POST = handler;
export const PATCH = handler;
export const DELETE = handler;
