# ═══════════════════════════════════════════════════════════════════
# Re:putation — Public Site (Next.js, Cloud Run)
#   - Multi-stage: 빌드 도구는 builder에만, 런타임은 standalone 번들만.
#   - Base pinned by digest (node:24-alpine) — 재현 가능한 빌드.
#   - 비루트 사용자 실행. Cloud Run이 PORT(기본 8080)를 주입한다.
#
# NEXT_PUBLIC_* 는 빌드 시점에 클라이언트 번들에 인라인되므로 build-arg로 전달:
#   docker build \
#     --build-arg NEXT_PUBLIC_API_URL=https://<domain>/api/v1/public \
#     --build-arg NEXT_PUBLIC_SITE_URL=https://<domain> \
#     --build-arg NEXT_PUBLIC_BACKEND_URL=https://<domain> \
#     -t site -f site/Dockerfile site
# ═══════════════════════════════════════════════════════════════════

# ── Builder stage ──────────────────────────────────────────────────
FROM node:24-alpine@sha256:2bdb65ed1dab192432bc31c95f94155ca5ad7fc1392fb7eb7526ab682fa5bf14 AS builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_SITE_URL
ARG NEXT_PUBLIC_BACKEND_URL
ARG NEXT_PUBLIC_GCP_STORAGE_BUCKET
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    NEXT_PUBLIC_BACKEND_URL=$NEXT_PUBLIC_BACKEND_URL \
    NEXT_PUBLIC_GCP_STORAGE_BUCKET=$NEXT_PUBLIC_GCP_STORAGE_BUCKET \
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

# standalone 번들 = server.js + 필요한 node_modules만. static/public은 별도 복사.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

USER node
EXPOSE 8080

# Cloud Run은 자체 startup/liveness probe를 쓴다 — 이 HEALTHCHECK는 로컬 docker 용.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -q --spider "http://127.0.0.1:${PORT}/" || exit 1

CMD ["node", "server.js"]
