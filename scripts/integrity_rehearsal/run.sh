#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SOURCE="$ROOT/scripts/integrity_rehearsal"
OLD_SHA=41f61d6f2f47459e3135dac66e430e454a1f1735
OLD_REGISTRY="$ROOT/.omo/evidence/rehearsal-prep/baseline-resource-registry.json"
COMPATIBLE_MANIFEST="${COMPATIBLE_READER_MANIFEST:-$SOURCE/fixtures/compatible-reader.json}"
REQUIRED=(runtime.env compose.yaml fixture.py check.py browser.mjs browser_error_classifier.mjs test_browser_error_classifier.mjs test_capture_selectors.mjs admin_availability.mjs test_admin_availability.mjs rollback_site_smoke.mjs browser.Dockerfile browser-package.json package-lock.json harnesslib.py scenarios.py sitecustomize.py)

fail() {
  printf 'integrity rehearsal preflight failed: %s\n' "$*" >&2
  exit 1
}

require_scaffold() {
  local name
  for name in "${REQUIRED[@]}"; do
    test -s "$SOURCE/$name" || fail "required fixture is missing or empty: $name"
  done
  git -C "$ROOT" cat-file -e "$OLD_SHA^{commit}" || fail "exact baseline commit is unavailable"
  test "$(git -C "$ROOT" rev-parse "$OLD_SHA")" = "$OLD_SHA" || fail "baseline SHA does not resolve exactly"
  python3 "$SOURCE/test_scaffold.py" "$SOURCE"
  node "$SOURCE/test_browser_error_classifier.mjs"
  node "$SOURCE/test_admin_availability.mjs"
  local scaffold_artifacts="$ROOT/.omo/evidence/task-16/compose-artifacts"
  mkdir -p "$scaffold_artifacts"
  local services
  services=$(env REHEARSAL_ARTIFACTS="$scaffold_artifacts" \
    OLD_IMAGE=old:scaffold OLD_SOURCE_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    NEW_IMAGE=new:scaffold NEW_SOURCE_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
    COMPATIBLE_IMAGE=compatible:scaffold COMPATIBLE_SOURCE_SHA=cccccccccccccccccccccccccccccccccccccccc \
    SITE_IMAGE=site:scaffold ADMIN_IMAGE=admin:scaffold BROWSER_IMAGE=browser:scaffold \
    docker compose --env-file /dev/null -f "$SOURCE/compose.yaml" config --services)
  local role
  for role in postgres redis migrate-new fixture api-old worker-old api-new worker-new beat-new \
    api-compatible site-new admin-new browser probe; do
    grep -Fxq "$role" <<<"$services" || fail "Compose role is missing: $role"
  done
  test "$(grep -c . <<<"$services")" -eq 14 \
    || fail "Compose must resolve exactly 14 isolated services (13 execution roles plus probe)"
  if grep -Fxq worker-compatible <<<"$services"; then
    fail "compatible writer role must not exist"
  fi
  if grep -Fxq beat-compatible <<<"$services"; then
    fail "compatible Beat role must not exist"
  fi
}

validate_full_inputs() {
  test -s "$COMPATIBLE_MANIFEST" || fail "compatible reader checkpoint manifest is not ready: $COMPATIBLE_MANIFEST"
  test "${RUNTIME_SOURCE_SHA:-}" != "" || fail "full rehearsal requires RUNTIME_SOURCE_SHA"
  test "${#RUNTIME_SOURCE_SHA}" = 40 || fail "RUNTIME_SOURCE_SHA must be a full SHA"
  git -C "$ROOT" cat-file -e "$RUNTIME_SOURCE_SHA^{commit}" || fail "runtime source commit is unavailable"
  test "$(git -C "$ROOT" rev-parse "$RUNTIME_SOURCE_SHA")" = "$RUNTIME_SOURCE_SHA" \
    || fail "runtime source SHA does not resolve exactly"
  PYTHONPATH="$SOURCE" python3 - "$COMPATIBLE_MANIFEST" "$ROOT" <<'PY'
import sys
from pathlib import Path
from harnesslib import read_compatible_checkpoint
read_compatible_checkpoint(Path(sys.argv[1]), repo_root=Path(sys.argv[2]))
PY
}

if test "${1:-}" = "--scaffold-check"; then
  require_scaffold
  printf '{"status":"scaffold_ready","finalExecution":"requires_full_rehearsal"}\n'
  exit 0
fi

if test "${1:-}" = "--bootstrap-check"; then
  require_scaffold
  validate_full_inputs
  baseline_source=build_pinned_commit
  if test -s "$OLD_REGISTRY"; then
    PYTHONPATH="$SOURCE" python3 - "$OLD_REGISTRY" <<'PY'
import sys
from pathlib import Path
from harnesslib import read_single_resource
read_single_resource(Path(sys.argv[1]), "old")
PY
    baseline_source=validated_local_cache
  fi
  printf '{"status":"bootstrap_ready","runtimeSourceSha":"%s","compatibleManifest":"%s","baselineSource":"%s"}\n' \
    "$RUNTIME_SOURCE_SHA" "$COMPATIBLE_MANIFEST" "$baseline_source"
  exit 0
fi

require_scaffold
MODE=full
ROLLBACK_SMOKE=0
case "${1:-}" in
  --component-run) MODE=component ;;
  --rollback-smoke) ROLLBACK_SMOKE=1; validate_full_inputs ;;
  '') validate_full_inputs ;;
  *) fail "unknown mode: $1" ;;
esac

RUN=$(mktemp -d "${TMPDIR:-/tmp}/reputation-task16.XXXXXX")
RUN_ID=$(basename "$RUN" | tr '[:upper:].' '[:lower:]-')
ARTIFACTS="$ROOT/.omo/evidence/task-16/runs/$RUN_ID"
REGISTRY="$ROOT/.omo/evidence/task-16/resources.json"
mkdir -p "$ARTIFACTS" "$RUN/new-context" "$RUN/old-context" "$RUN/old-export" \
  "$RUN/compatible-context" "$RUN/compatible-export" "$RUN/new-source" "$RUN/docker-config"
chmod 755 "$RUN"
# The backend probe runs as appuser, while GitHub Actions owns this bind source.
# Keep the writable scope to this disposable per-run artifact directory.
chmod 1777 "$ARTIFACTS"

DOCKER_ENDPOINT=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
case "$DOCKER_ENDPOINT" in unix://*) ;; *) fail "a local Docker socket is required" ;; esac
COMPOSE=(docker compose)
if command -v docker-compose >/dev/null 2>&1; then COMPOSE=("$(command -v docker-compose)"); fi

export REHEARSAL_ID="$RUN_ID"
export REHEARSAL_ARTIFACTS="$ARTIFACTS"
export OLD_SOURCE_SHA="$OLD_SHA"
export NEW_SOURCE_SHA="${RUNTIME_SOURCE_SHA:-$(git -C "$ROOT" rev-parse HEAD)}"
OLD_IMAGE_BUILT=0

cp "$SOURCE"/*.py "$SOURCE"/*.mjs "$SOURCE/compose.yaml" "$SOURCE/runtime.env" "$RUN/"

copy_backend_allowlist() {
  local source_root=$1
  local destination=$2
  local entry
  for entry in Dockerfile docker-entrypoint.sh pyproject.toml uv.lock alembic.ini app alembic; do
    cp -R "$source_root/backend/$entry" "$destination/"
  done
  cp "$source_root/backend/.dockerignore" "$destination/"
}
if test "$MODE" = full; then
  git -C "$ROOT" archive "$NEW_SOURCE_SHA" backend admin site | tar -x -C "$RUN/new-source"
  NEW_SOURCE_ROOT="$RUN/new-source"
else
  NEW_SOURCE_ROOT="$ROOT"
fi
copy_backend_allowlist "$NEW_SOURCE_ROOT" "$RUN/new-context"

if find "$RUN/new-context" -type f \( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' \) -print -quit | grep -q .; then
  fail "forbidden secret-shaped file entered the new image context"
fi

OLD_EXPECTED_ID=
OLD_EXPECTED_DIGEST=
BASELINE_REGISTRY_USED=0
if test -s "$OLD_REGISTRY"; then
eval "$(PYTHONPATH="$SOURCE" python3 - "$OLD_REGISTRY" <<'PY'
import shlex
import sys
from pathlib import Path
from harnesslib import read_single_resource
old = read_single_resource(Path(sys.argv[1]), "old")
for key, value in {
    "OLD_IMAGE": old.tag,
    "OLD_EXPECTED_ID": old.image_id,
    "OLD_EXPECTED_DIGEST": old.digest,
}.items():
    print(f"export {key}={shlex.quote(value)}")
PY
)"
BASELINE_REGISTRY_USED=1
else
OLD_IMAGE="reputation-refactor-old:$OLD_SHA-$RUN_ID"
fi
if test "$MODE" = full; then
eval "$(PYTHONPATH="$SOURCE" python3 - "$COMPATIBLE_MANIFEST" "$ROOT" <<'PY'
import shlex
import sys
from pathlib import Path
from harnesslib import read_compatible_checkpoint
compatible = read_compatible_checkpoint(Path(sys.argv[1]), repo_root=Path(sys.argv[2]))
print(f"export COMPATIBLE_SOURCE_SHA={shlex.quote(compatible.source_sha)}")
PY
)"
else
COMPATIBLE_SOURCE_SHA=0000000000000000000000000000000000000000
fi

NEW_IMAGE="reputation-refactor-new:$NEW_SOURCE_SHA-$RUN_ID"
COMPATIBLE_IMAGE="reputation-refactor-compatible:$COMPATIBLE_SOURCE_SHA-$RUN_ID"
SITE_IMAGE="reputation-refactor-site:$NEW_SOURCE_SHA-$RUN_ID"
SITE_NEW_IMAGE="$SITE_IMAGE"
SITE_ROLLBACK_IMAGE="reputation-refactor-site-rollback:$NEW_SOURCE_SHA-$RUN_ID"
ADMIN_IMAGE="reputation-refactor-admin:$NEW_SOURCE_SHA-$RUN_ID"
BROWSER_IMAGE="reputation-refactor-browser:1.59.1-$RUN_ID"
export NEW_IMAGE SITE_IMAGE SITE_ROLLBACK_IMAGE ADMIN_IMAGE BROWSER_IMAGE COMPATIBLE_SOURCE_SHA

compose() {
  env -i PATH="$PATH" HOME="$RUN" DOCKER_CONFIG="$RUN/docker-config" DOCKER_HOST="$DOCKER_ENDPOINT" \
    REHEARSAL_ID="$REHEARSAL_ID" REHEARSAL_ARTIFACTS="$REHEARSAL_ARTIFACTS" \
    OLD_IMAGE="$OLD_IMAGE" OLD_SOURCE_SHA="$OLD_SOURCE_SHA" NEW_IMAGE="$NEW_IMAGE" \
    NEW_SOURCE_SHA="$NEW_SOURCE_SHA" COMPATIBLE_IMAGE="$COMPATIBLE_IMAGE" \
    COMPATIBLE_SOURCE_SHA="$COMPATIBLE_SOURCE_SHA" SITE_IMAGE="$SITE_IMAGE" ADMIN_IMAGE="$ADMIN_IMAGE" \
    BROWSER_IMAGE="$BROWSER_IMAGE" SITE_BACKEND_URL="${SITE_BACKEND_URL:-http://api-new:8000}" \
    BROWSER_API_URL="${BROWSER_API_URL:-http://api-new:8000}" \
    BROWSER_MODE="${BROWSER_MODE:-new-flow}" \
    "${COMPOSE[@]}" --project-name "$REHEARSAL_ID" --env-file /dev/null --file "$RUN/compose.yaml" "$@"
}

docker_clean() {
  env -i PATH="$PATH" HOME="$RUN" DOCKER_CONFIG="$RUN/docker-config" DOCKER_HOST="$DOCKER_ENDPOINT" docker "$@"
}

write_registry() {
  PYTHONPATH="$SOURCE" python3 - "$REGISTRY" "$RUN_ID" "$ARTIFACTS" "$OLD_IMAGE" "$NEW_IMAGE" "$COMPATIBLE_IMAGE" "$SITE_NEW_IMAGE" "$SITE_ROLLBACK_IMAGE" "$ADMIN_IMAGE" "$BROWSER_IMAGE" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "task": "16",
    "status": "running",
    "dockerProject": sys.argv[2],
    "artifacts": sys.argv[3],
    "resources": [{"kind": "docker-image", "role": role, "name": name} for role, name in zip(
        ("old", "new", "compatible", "site", "site-rollback", "admin", "browser"), sys.argv[4:]
    )],
    "cleanup": {"command": f"docker compose --project-name {sys.argv[2]} down --volumes --remove-orphans"},
}
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

cleanup() {
  local status=$?
  trap - EXIT
  compose logs --no-color > "$ARTIFACTS/containers.log" 2>&1 || true
  compose ps --all > "$ARTIFACTS/containers.txt" 2>&1 || true
  compose down --volumes --remove-orphans >> "$ARTIFACTS/cleanup-receipt.txt" 2>&1 || true
  docker_clean image rm "$NEW_IMAGE" "$COMPATIBLE_IMAGE" "$SITE_NEW_IMAGE" "$SITE_ROLLBACK_IMAGE" "$ADMIN_IMAGE" "$BROWSER_IMAGE" >> "$ARTIFACTS/cleanup-receipt.txt" 2>&1 || true
  if test "$OLD_IMAGE_BUILT" = 1; then
    docker_clean image rm "$OLD_IMAGE" >> "$ARTIFACTS/cleanup-receipt.txt" 2>&1 || true
  fi
  printf 'exit_status=%s\nproject=%s\npreserved_old=%s\nremoved_compatible=%s\n' \
    "$status" "$RUN_ID" "$OLD_IMAGE" "$COMPATIBLE_IMAGE" >> "$ARTIFACTS/cleanup-receipt.txt"
  printf '%s\n' "$status" > "$ARTIFACTS/exit-status.txt"
  python3 - "$REGISTRY" "$status" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
if path.exists():
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "cleaned" if sys.argv[2] == "0" else "failed_and_cleaned"
    value["exitStatus"] = int(sys.argv[2])
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
  rm -rf "$RUN"
  printf 'Evidence: %s (exit %s)\n' "$ARTIFACTS" "$status"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
write_registry

if test "$MODE" = full; then
  git -C "$ROOT" cat-file -e "$COMPATIBLE_SOURCE_SHA^{commit}" \
    || fail "compatible reader commit is unavailable: $COMPATIBLE_SOURCE_SHA"
  git -C "$ROOT" archive "$COMPATIBLE_SOURCE_SHA" \
    backend/Dockerfile backend/docker-entrypoint.sh backend/pyproject.toml backend/uv.lock \
    backend/alembic.ini backend/.dockerignore backend/app backend/alembic \
    | tar -x -C "$RUN/compatible-export"
  for entry in Dockerfile docker-entrypoint.sh pyproject.toml uv.lock alembic.ini .dockerignore app alembic; do
    cp -R "$RUN/compatible-export/backend/$entry" "$RUN/compatible-context/"
  done
  if find "$RUN/compatible-context" -type f \( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' \) -print -quit | grep -q .; then
    fail "forbidden secret-shaped file entered the compatible image context"
  fi
fi

BUILDX_PATH=$(docker info --format '{{range .ClientInfo.Plugins}}{{if eq .Name "buildx"}}{{.Path}}{{end}}{{end}}')
test -n "$BUILDX_PATH" && test -x "$BUILDX_PATH" || fail "Docker Buildx plugin is required"
BUILDX=("$BUILDX_PATH")
build() {
  env -i PATH="$PATH" HOME="$RUN" DOCKER_CONFIG="$RUN/docker-config" DOCKER_HOST="$DOCKER_ENDPOINT" \
    "${BUILDX[@]}" build --load --platform linux/amd64 --progress plain "$@"
}
if ! docker_clean image inspect "$OLD_IMAGE" >/dev/null 2>&1; then
  git -C "$ROOT" archive "$OLD_SHA" \
    backend/Dockerfile backend/docker-entrypoint.sh backend/pyproject.toml backend/uv.lock \
    backend/alembic.ini backend/.dockerignore backend/app backend/alembic \
    | tar -x -C "$RUN/old-export"
  for entry in Dockerfile docker-entrypoint.sh pyproject.toml uv.lock alembic.ini .dockerignore app alembic; do
    cp -R "$RUN/old-export/backend/$entry" "$RUN/old-context/"
  done
  if find "$RUN/old-context" -type f \( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' \) -print -quit | grep -q .; then
    fail "forbidden secret-shaped file entered the old image context"
  fi
  build --label "org.opencontainers.image.revision=$OLD_SHA" --label com.motionlabs.task=task-16 \
    --tag "$OLD_IMAGE" "$RUN/old-context" 2>&1 | tee "$ARTIFACTS/old-build.log"
  OLD_IMAGE_BUILT=1
fi
OLD_ACTUAL_ID=$(docker_clean image inspect "$OLD_IMAGE" --format '{{.Id}}')
if test "$BASELINE_REGISTRY_USED" = 1; then
  test "$OLD_ACTUAL_ID" = "$OLD_EXPECTED_ID" \
    || fail "baseline image digest mismatch: expected $OLD_EXPECTED_ID got $OLD_ACTUAL_ID"
fi
build --label "org.opencontainers.image.revision=$NEW_SOURCE_SHA" --label com.motionlabs.task=task-16 \
  --tag "$NEW_IMAGE" "$RUN/new-context" 2>&1 | tee "$ARTIFACTS/new-build.log"
if test "$MODE" = full; then
  build --label "org.opencontainers.image.revision=$COMPATIBLE_SOURCE_SHA" --label com.motionlabs.task=task-16 \
    --tag "$COMPATIBLE_IMAGE" "$RUN/compatible-context" 2>&1 | tee "$ARTIFACTS/compatible-build.log"
fi
build --build-arg NEXT_PUBLIC_API_URL=http://api-new:8000/api/v1/public \
  --build-arg NEXT_PUBLIC_SITE_URL=https://reputation.rehearsal.example.test \
  --build-arg NEXT_PUBLIC_BACKEND_URL=http://api-new:8000 \
  --label "org.opencontainers.image.revision=$NEW_SOURCE_SHA" --tag "$SITE_IMAGE" -f "$NEW_SOURCE_ROOT/site/Dockerfile" "$NEW_SOURCE_ROOT/site" \
  2>&1 | tee "$ARTIFACTS/site-build.log"
if test "$MODE" = full; then
  build --build-arg NEXT_PUBLIC_API_URL=http://api-compatible:8000/api/v1/public \
    --build-arg NEXT_PUBLIC_SITE_URL=https://reputation.rehearsal.example.test \
    --build-arg NEXT_PUBLIC_BACKEND_URL=http://api-compatible:8000 \
    --label "org.opencontainers.image.revision=$NEW_SOURCE_SHA" --tag "$SITE_ROLLBACK_IMAGE" \
    -f "$NEW_SOURCE_ROOT/site/Dockerfile" "$NEW_SOURCE_ROOT/site" \
    2>&1 | tee "$ARTIFACTS/site-rollback-build.log"
  docker_clean image inspect "$SITE_ROLLBACK_IMAGE" --format '{{json .}}' \
    > "$ARTIFACTS/site-rollback-image-inspect.json"
  docker_clean image inspect "$SITE_IMAGE" --format '{{json .}}' \
    > "$ARTIFACTS/site-new-image-inspect.json"
  SITE_NEW_ID=$(docker_clean image inspect "$SITE_IMAGE" --format '{{.Id}}')
  SITE_ROLLBACK_ID=$(docker_clean image inspect "$SITE_ROLLBACK_IMAGE" --format '{{.Id}}')
  SITE_NEW_REVISION=$(docker_clean image inspect "$SITE_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  SITE_ROLLBACK_REVISION=$(docker_clean image inspect "$SITE_ROLLBACK_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  test "$SITE_NEW_ID" != "$SITE_ROLLBACK_ID" || fail "new-flow and rollback Site images must be distinct"
  test "$SITE_NEW_REVISION" = "$NEW_SOURCE_SHA" || fail "new-flow Site source label mismatch"
  test "$SITE_ROLLBACK_REVISION" = "$NEW_SOURCE_SHA" || fail "rollback Site source label mismatch"
  python3 - "$ARTIFACTS/site-image-identities.json" "$SITE_IMAGE" "$SITE_NEW_ID" \
    "$SITE_ROLLBACK_IMAGE" "$SITE_ROLLBACK_ID" "$NEW_SOURCE_SHA" <<'PY'
import json
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "status": "assertions_passed",
    "sourceSha": sys.argv[6],
    "images": {
        "newFlow": {"tag": sys.argv[2], "imageId": sys.argv[3]},
        "rollback": {"tag": sys.argv[4], "imageId": sys.argv[5]},
    },
    "distinctImageIds": sys.argv[3] != sys.argv[5],
    "sourceLabelsMatch": True,
}, indent=2) + "\n", encoding="utf-8")
PY
fi
build --build-arg NEXT_PUBLIC_BACKEND_URL=http://api-new:8000 \
  --label "org.opencontainers.image.revision=$NEW_SOURCE_SHA" --tag "$ADMIN_IMAGE" -f "$NEW_SOURCE_ROOT/admin/Dockerfile" "$NEW_SOURCE_ROOT/admin" \
  2>&1 | tee "$ARTIFACTS/admin-build.log"
build --label com.motionlabs.task=task-16 --tag "$BROWSER_IMAGE" \
  -f "$SOURCE/browser.Dockerfile" "$SOURCE" 2>&1 | tee "$ARTIFACTS/browser-build.log"

identity_specs=("old:$OLD_IMAGE:$OLD_SOURCE_SHA" "new:$NEW_IMAGE:$NEW_SOURCE_SHA")
if test "$MODE" = full; then identity_specs+=("compatible:$COMPATIBLE_IMAGE:$COMPATIBLE_SOURCE_SHA"); fi
for spec in "${identity_specs[@]}"; do
  role=${spec%%:*}
  rest=${spec#*:}
  sha=${rest##*:}
  image=${rest%:*}
  docker_clean image inspect "$image" --format '{{json .}}' > "$ARTIFACTS/$role-image-inspect.json"
  printf '%s\n' "$sha" > "$ARTIFACTS/source-commit-$role.txt"
done
if test "$MODE" = full; then
  PYTHONPATH="$SOURCE" python3 "$SOURCE/check.py" image-identities --artifacts "$ARTIFACTS" \
    --old "$OLD_IMAGE" --new "$NEW_IMAGE" --compatible "$COMPATIBLE_IMAGE"
fi

if test "$ROLLBACK_SMOKE" = 1; then
  export SITE_BACKEND_URL=http://api-compatible:8000
  export BROWSER_API_URL=http://api-compatible:8000
  SITE_IMAGE="$SITE_ROLLBACK_IMAGE"
  export SITE_IMAGE
  compose config > "$ARTIFACTS/compose.rollback-smoke.resolved.yaml"
  compose config --services > "$ARTIFACTS/services.txt"
  compose up -d --wait --wait-timeout 90 postgres redis
  test "$(docker_clean network inspect "${REHEARSAL_ID}_isolated" --format '{{.Internal}}')" = true \
    || fail "rehearsal network is not internal"
  compose run --rm migrate-new
  compose up -d --wait --wait-timeout 300 fixture
  compose run --rm --no-deps probe python /rehearsal/check.py rollback-smoke-seed
  compose up -d --wait --wait-timeout 120 api-compatible site-new
  compose run --rm browser node /rehearsal/rollback_site_smoke.mjs \
    --api-url http://api-compatible:8000 \
    --site-url http://reputation.rehearsal.example.test:3000 \
    --output-dir /artifacts/rollback-site-smoke \
    2>&1 | tee "$ARTIFACTS/rollback-site-smoke.log"
  printf '{"status":"ROLLBACK_SITE_SMOKE_PASS","fullTask16Credited":false}\n' > "$ARTIFACTS/result.json"
  exit 0
fi

compose config > "$ARTIFACTS/compose.resolved.yaml"
compose config --services > "$ARTIFACTS/services.txt"
compose up -d --wait --wait-timeout 90 postgres redis
test "$(docker_clean network inspect "${REHEARSAL_ID}_isolated" --format '{{.Internal}}')" = true || fail "rehearsal network is not internal"
compose run --rm migrate-new
compose run --rm --no-deps probe python /rehearsal/check.py migration

# Baseline characterization is input-only. It never becomes the rollback reader.
compose up -d --wait --wait-timeout 300 fixture
compose up -d --wait --wait-timeout 120 api-old worker-old
compose run --rm --no-deps probe python /rehearsal/check.py baseline-characterize
compose exec -T api-old python /rehearsal/check.py legacy-mutations
compose stop worker-old
compose exec -T api-old python /rehearsal/check.py old-queue-prime
compose stop api-old

# New revision owns all mutations through drain, reconciliation, and pointer switch.
compose up -d --wait --wait-timeout 120 api-new worker-new beat-new
compose run --rm --no-deps probe python /rehearsal/check.py writer-canaries
compose run --rm --no-deps probe python /rehearsal/check.py old-queue-drain
compose run --rm --no-deps probe python /rehearsal/check.py synthetic-flow
compose run --rm --no-deps probe python /rehearsal/check.py worker-loss-prime
compose kill -s SIGKILL worker-new
compose run --rm --no-deps probe python /rehearsal/check.py worker-loss-observe
compose up -d --wait --wait-timeout 120 worker-new
compose run --rm --no-deps probe python /rehearsal/check.py worker-loss-recover
compose stop worker-old
compose run --rm --no-deps probe python /rehearsal/check.py old-drained
compose run --rm --no-deps probe python /rehearsal/check.py legacy-incident-reconciliation
compose run --rm --no-deps probe python -m app.utils.legacy_publish_retirement_preflight \
  | tee "$ARTIFACTS/legacy-publish-retirement-preflight.json"
compose stop worker-new beat-new admin-new 2>/dev/null || true
compose run --rm --no-deps probe python /rehearsal/check.py post-drain-reconcile
compose up -d --wait --wait-timeout 120 worker-new beat-new
compose run --rm --no-deps probe python /rehearsal/check.py switch
compose up -d --wait --wait-timeout 120 site-new admin-new
compose run --rm --no-deps probe python /rehearsal/check.py pdf
compose run --rm --no-deps probe python /rehearsal/check.py negative-boundaries
BROWSER_MODE=new-flow
export BROWSER_MODE
compose run --rm browser 2>&1 | tee "$ARTIFACTS/browser-new-flow.log"
compose run --rm --no-deps probe python /rehearsal/check.py report-delivery
compose run --rm --no-deps probe python /rehearsal/check.py candidate-pass
BROWSER_MODE=verify-pass
export BROWSER_MODE
compose run --rm browser 2>&1 | tee "$ARTIFACTS/browser-verify-pass.log"
compose run --rm --no-deps probe python /rehearsal/check.py immutable-history
if test "$MODE" = component; then
  compose run --rm --no-deps probe python /rehearsal/check.py component-final
  printf '{"status":"NON_FINAL_COMPONENT_PASS","fullTask16Credited":false}\n' > "$ARTIFACTS/result.json"
  exit 0
fi

# Rollback is read-only: all mutation-capable roles remain stopped.
compose stop worker-new beat-new admin-new api-new
export SITE_BACKEND_URL=http://api-compatible:8000
export BROWSER_API_URL=http://api-compatible:8000
SITE_IMAGE="$SITE_ROLLBACK_IMAGE"
export SITE_IMAGE
BROWSER_MODE=rollback
export BROWSER_MODE
compose config > "$ARTIFACTS/compose.rollback.resolved.yaml"
compose up -d --wait --wait-timeout 120 api-compatible site-new
compose run --rm --no-deps probe python /rehearsal/check.py rollback
compose run --rm browser 2>&1 | tee "$ARTIFACTS/browser-rollback.log"
compose run --rm --no-deps probe python /rehearsal/check.py final
printf '{"status":"PASS","scope":"task-16B-complete"}\n' > "$ARTIFACTS/result.json"
