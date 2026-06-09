# ═══════════════════════════════════════════════════════════════════
# Re:putation — Admin Console (Next.js, Cloud Run)
#   - Multi-stage: 빌드 도구는 builder에만, 런타임은 standalone 번들만.
#   - Base pinned by digest (node:24-alpine) — 재현 가능한 빌드.
#   - 비루트 사용자 실행. Cloud Run이 PORT(기본 8080)를 주입한다.
#
# NEXT_PUBLIC_* 는 빌드 시점에 클라이언트 번들에 인라인되므로 build-arg로 전달:
#   docker build \
#     --build-arg NEXT_PUBLIC_BACKEND_URL=https://<domain> \
#     -t admin -f admin/Dockerfile admin
# 런타임 secret(ADMIN_SESSION_SECRET, ADMIN_SECRET_KEY)과 BACKEND_URL은
# Cloud Run env/Secret Manager로 주입 — 이미지에 굽지 않는다.
# ═══════════════════════════════════════════════════════════════════

# ── Builder stage ──────────────────────────────────────────────────
FROM node:24-alpine@sha256:2bdb65ed1dab192432bc31c95f94155ca5ad7fc1392fb7eb7526ab682fa5bf14 AS builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

ARG NEXT_PUBLIC_BACKEND_URL
ENV NEXT_PUBLIC_BACKEND_URL=$NEXT_PUBLIC_BACKEND_URL \
    NEXT_TELEMETRY_DISABLED=1

COPY . .
RUN npm run build

# ── Runtime stage ──────────────────────────────────────────────────
FROM node:24-alpine@sha256:2bdb65ed1dab192432bc31c95f94155ca5ad7fc1392fb7eb7526ab682fa5bf14 AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=8080

COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
# admin에는 public/ 디렉토리가 없다 (정적 자산 없음).

USER node
EXPOSE 8080

# Cloud Run은 자체 startup/liveness probe를 쓴다 — 이 HEALTHCHECK는 로컬 docker 용.
# /login은 미인증 상태에서도 200을 반환하는 가장 가벼운 페이지.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -q --spider "http://127.0.0.1:${PORT}/login" || exit 1

CMD ["node", "server.js"]
