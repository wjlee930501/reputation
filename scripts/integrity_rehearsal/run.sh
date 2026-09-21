#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SOURCE="$ROOT/scripts/integrity_rehearsal"
RUN=$(mktemp -d "${TMPDIR:-/tmp}/integrity-rehearsal.XXXXXX")
ARTIFACTS="$SOURCE/artifacts/$(basename "$RUN")"
chmod 755 "$RUN"
mkdir -p "$ARTIFACTS" "$RUN/context" "$RUN/docker-config"
cp "$SOURCE"/*.py "$SOURCE/compose.yaml" "$SOURCE/runtime.env" "$RUN/"
# Allowlist build inputs: no repository .env, credentials, retained stack, or test code.
for entry in Dockerfile docker-entrypoint.sh pyproject.toml uv.lock alembic.ini app alembic; do
  cp -R "$ROOT/backend/$entry" "$RUN/context/"
done
cp "$ROOT/backend/.dockerignore" "$RUN/context/"
REHEARSAL_ID=$(basename "$RUN" | tr '[:upper:].' '[:lower:]-')
export REHEARSAL_ID
DOCKER_ENDPOINT=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
case "$DOCKER_ENDPOINT" in unix://*) ;; *) echo 'A local Docker socket is required' >&2; exit 1 ;; esac
COMPOSE=(docker compose)
if command -v docker-compose >/dev/null 2>&1; then COMPOSE=("$(command -v docker-compose)"); fi
git -C "$ROOT" rev-parse HEAD > "$ARTIFACTS/source-commit.txt"
git -C "$ROOT" status --porcelain > "$ARTIFACTS/source-status.txt"
compose() {
  env -i PATH="$PATH" HOME="$RUN" DOCKER_CONFIG="$RUN/docker-config" DOCKER_HOST="$DOCKER_ENDPOINT" \
    REHEARSAL_ID="$REHEARSAL_ID" \
    "${COMPOSE[@]}" --project-name "$REHEARSAL_ID" --env-file /dev/null \
    --file "$RUN/compose.yaml" "$@"
}
cleanup() {
  status=$?
  trap - EXIT
  compose logs --no-color > "$ARTIFACTS/containers.log" 2>&1 || true
  compose ps --all > "$ARTIFACTS/containers.txt" 2>&1 || true
  compose down --volumes --remove-orphans >> "$ARTIFACTS/cleanup.log" 2>&1 || true
  env -i PATH="$PATH" HOME="$RUN" DOCKER_CONFIG="$RUN/docker-config" DOCKER_HOST="$DOCKER_ENDPOINT" \
    docker image rm "integrity-rehearsal:$REHEARSAL_ID" >> "$ARTIFACTS/cleanup.log" 2>&1 || true
  printf '%s\n' "$status" > "$ARTIFACTS/exit-status.txt"
  rm -rf "$RUN"
  printf 'Evidence: %s (exit %s)\n' "$ARTIFACTS" "$status"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
compose config > "$ARTIFACTS/compose.resolved.yaml"
BUILDX_PATH=$(docker info --format '{{range .ClientInfo.Plugins}}{{if eq .Name "buildx"}}{{.Path}}{{end}}{{end}}')
[ -n "$BUILDX_PATH" ] && [ -x "$BUILDX_PATH" ] || { echo 'Docker Buildx plugin is required' >&2; exit 1; }
BUILDX=("$BUILDX_PATH")
env -i PATH="$PATH" HOME="$RUN" DOCKER_CONFIG="$RUN/docker-config" DOCKER_HOST="$DOCKER_ENDPOINT" \
  "${BUILDX[@]}" build --load --platform linux/amd64 --progress plain \
  --label "org.opencontainers.image.revision=$(cat "$ARTIFACTS/source-commit.txt")" \
  --tag "integrity-rehearsal:$REHEARSAL_ID" "$RUN/context" 2>&1 | tee "$ARTIFACTS/build.log"
env -i PATH="$PATH" HOME="$RUN" DOCKER_CONFIG="$RUN/docker-config" DOCKER_HOST="$DOCKER_ENDPOINT" \
  docker image inspect "integrity-rehearsal:$REHEARSAL_ID" \
  --format '{{.Id}}' > "$ARTIFACTS/image-id.txt"
compose up -d --wait --wait-timeout 90 postgres redis
internal=$(env -i PATH="$PATH" HOME="$RUN" DOCKER_CONFIG="$RUN/docker-config" DOCKER_HOST="$DOCKER_ENDPOINT" \
  docker network inspect "${REHEARSAL_ID}_isolated" --format '{{.Internal}}')
[ "$internal" = true ] || { printf 'Network is not internal\n' >&2; exit 1; }
compose run --rm --no-deps migrate
compose up -d fixture api worker beat
# Stock entrypoints; probe from within each container, never publish host ports.
for service in api worker beat; do
  path=/ready
  if [ "$service" = api ]; then path=/health/live; fi
  ready=0
  for attempt in $(seq 1 60); do
    if compose exec -T "$service" curl -fsS "http://127.0.0.1:8000$path" > /dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  [ "$ready" = 1 ] || { printf 'Startup failed: %s\n' "$service" >&2; exit 1; }
done
compose run --rm --no-deps probe python /rehearsal/check.py smoke | tee "$ARTIFACTS/smoke.jsonl"
# Keep scheduling stock, then stop it before controlled fixture scenarios to avoid clock races.
compose stop beat
compose run --rm --no-deps probe python /rehearsal/check.py initial | tee "$ARTIFACTS/initial.jsonl"
compose kill -s SIGKILL worker
compose run --rm --no-deps probe python /rehearsal/check.py after-loss | tee "$ARTIFACTS/loss.jsonl"
compose up -d worker
compose run --rm --no-deps probe python /rehearsal/check.py recovered | tee "$ARTIFACTS/recovery.jsonl"
