.PHONY: setup up down logs migrate revision test test-db-setup demo-seed essence-backfill copy-guard admin-create-owner db-budget-guard
.PHONY: clinic-visual-readiness clinic-visual-seed-report clinic-visual-seed
.PHONY: deploy-api deploy-worker deploy-beat deploy-all deploy-migrate setup-gcp build-image

setup:
	cp .env.example .env
	docker compose up -d db redis
	sleep 4
	docker compose up -d
	sleep 6
	docker compose exec api alembic upgrade head
	@echo ""
	@echo "✅ Re:putation 개발 환경 준비 완료"
	@echo "   API Docs : http://localhost:8000/docs"
	@echo "   Flower   : http://localhost:5555"

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f api worker beat

migrate:
	docker compose exec api alembic upgrade head

revision:
	@read -p "Migration message: " msg; \
	docker compose exec api alembic revision --autogenerate -m "$$msg"

# 컨테이너 안에서 도는 DB 기반 테스트(~50개 파일)가 쓰는 별도 테스트 DB.
# compose의 db 서비스는 POSTGRES_DB=reputation 하나만 만들고, 호스트 포트는 5434지만
# 컨테이너 네트워크에서는 db:5432다. 테스트 DB·Redis URL에는 기본값이 없어서(tests/db_env.py)
# 아래 `test` 타깃이 필요한 변수를 전부 명시적으로 넘긴다 — 빠진 변수가 있으면 그 변수를
# 쓰는 테스트는 skip이 아니라 변수 이름을 밝힌 실패로 끝난다.
TEST_DB_PLAIN := postgresql://reputation:reputation@db:5432/reputation_test
TEST_DB_ASYNC := postgresql+asyncpg://reputation:reputation@db:5432/reputation_test
TEST_DB_SYNC  := postgresql+psycopg2://reputation:reputation@db:5432/reputation_test
# compose redis 서비스의 DB 번호 중 개발 앱이 쓰지 않는 것(.env.example은 /0). CI와 같은 번호 배치.
TEST_REDIS_APP         := redis://redis:6379/1
TEST_REDIS_COST_GUARD  := redis://redis:6379/2
TEST_REDIS_INTEGRATION := redis://redis:6379/3

test-db-setup:
	# 멱등 — 이미 있으면 CREATE DATABASE가 실패하고, 그 다음 SELECT가 "정말 있는지"를
	# 증명한다. 진짜 접속 불가는 두 번째 명령에서 시끄럽게 깨진다.
	-docker compose exec -T db psql -U reputation -d postgres -c "CREATE DATABASE reputation_test"
	docker compose exec -T db psql -U reputation -d reputation_test -c "SELECT 1" > /dev/null
	docker compose exec -T \
		-e DATABASE_URL="$(TEST_DB_ASYNC)" -e SYNC_DATABASE_URL="$(TEST_DB_SYNC)" \
		api alembic upgrade head

# 알려진 한계: 이 타깃은 아직 전체 스위트를 통과시키지 못한다. compose api는
# ./backend만 /app에 마운트하므로 리포 루트 파일(docker-compose.yml, site/, Makefile)을
# 읽는 계약 테스트가 FileNotFoundError로 깨진다. 이 타깃보다 넓은 문제다 — 전체 스위트는
# `make test-backend-local`(호스트 실행)이 정본이고, 이 타깃은 컨테이너 환경 자체를
# 검증하는 용도다. 테스트 DB 변수 목록은 .github/workflows/ci.yml backend 잡과 맞춘다.
# api 컨테이너는 env_file: .env로 개발 DB(reputation)·개발 Redis(/0)를 받으므로 앱 자체의
# DATABASE_URL·SYNC_DATABASE_URL·REDIS_URL도 반드시 덮어쓴다 — 테스트는 DB 이름이 `_test`로
# 끝나지 않으면 세션을 시작하지 않는다(tests/conftest.py).
# MIGRATION_UPGRADE_DATABASE_URL·REDELIVERY_TEST_SYNC_DATABASE_URL은 넘기지 않는다 — 두
# 테스트는 루프백 호스트(127.0.0.1/localhost)와 전용 DB(reputation_autonomy_migration,
# 49152~65535 포트의 reputation_redelivery_test)를 단언하는데 컨테이너의 db:5432로는
# 맞출 수 없다. 그래서 이 컨테이너에서 두 테스트는 skip이 아니라 변수 이름을 밝힌 실패로 끝난다.
test: test-db-setup
	# backend/Dockerfile builds the api image with `uv sync --locked --no-dev`, so
	# pytest isn't installed in the running container — sync the dev extra into the
	# image's venv first (UV_PROJECT_ENVIRONMENT pins the target explicitly; the
	# runtime stage only sets VIRTUAL_ENV, which uv project commands don't read),
	# then run tests through that synced environment with uv run --no-sync.
	# -u root: the runtime stage copies /opt/venv from the builder without chown and
	# then switches to `appuser`, so a sync as the default user dies on
	# "Permission denied" when it rewrites site-packages.
	docker compose exec -u root -e UV_PROJECT_ENVIRONMENT=/opt/venv api uv sync --locked --extra dev
	# pytest는 기본 사용자(appuser)로 돈다 — root로 돌리면 /app 바인드 마운트에
	# root 소유의 .pytest_cache/__pycache__가 호스트 워크트리에 남아 이후 로컬 실행이
	# 권한 오류로 깨진다. 캐시를 아예 만들지 않게 해서 원인을 없앤다.
	docker compose exec \
		-e UV_PROJECT_ENVIRONMENT=/opt/venv \
		-e PYTHONDONTWRITEBYTECODE=1 \
		-e DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e SYNC_DATABASE_URL="$(TEST_DB_SYNC)" \
		-e REDIS_URL="$(TEST_REDIS_APP)" \
		-e COST_GUARD_REDIS_URL="$(TEST_REDIS_COST_GUARD)" \
		-e INTEGRATION_REDIS_URL="$(TEST_REDIS_INTEGRATION)" \
		-e INTEGRATION_DATABASE_URL="$(TEST_DB_PLAIN)" \
		-e TASK16_DATABASE_URL="$(TEST_DB_PLAIN)" \
		-e TASK22_DATABASE_URL="$(TEST_DB_PLAIN)" \
		-e TASK24_DATABASE_URL="$(TEST_DB_PLAIN)" \
		-e INCIDENT_TEST_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e OPERATION_RUN_SIGNAL_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e OPERATION_RUNS_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e OPERATION_RUN_TRANSITIONS_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e OPERATION_RUN_CONCURRENCY_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e NOTIFICATION_OUTBOX_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e ONBOARDING_PROJECTOR_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e CONTENT_PUBLISH_RECOVERY_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e TASK13_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e TASK18_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e TASK19_ASYNC_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e TASK20_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e OPERATIONS_TEST_DATABASE_URL="$(TEST_DB_SYNC)" \
		-e OPERATION_RUN_SIGNAL_SYNC_DATABASE_URL="$(TEST_DB_SYNC)" \
		-e TASK19_SYNC_DATABASE_URL="$(TEST_DB_SYNC)" \
		api uv run --no-sync pytest -v -p no:cacheprovider

test-local: test-backend-local test-frontend copy-guard

# 테스트 DB·Redis URL에는 기본값이 없다 — 아래 변수를 호스트에서 직접 export해야 하며, 빠지거나
# 그 DB에 접속하지 못하면 그 변수를 쓰는 테스트가 변수 이름과 함께 실패한다. 값·드라이버 스킴의 정본은
# .github/workflows/ci.yml backend 잡 env다. MIGRATION_UPGRADE_DATABASE_URL·
# REDELIVERY_TEST_SYNC_DATABASE_URL은 다른 변수와 떨어진 전용 DB를 가리켜야 한다(README 참고).
# 앱 자체의 URL(TEST_APP_URL_VARS)도 기본값이 없다. DATABASE_URL·SYNC_DATABASE_URL의 DB 이름은
# `_test`로 끝나야 하고, Redis는 개발 앱과 다른 DB 번호를 쓴다.
TEST_APP_URL_VARS := DATABASE_URL SYNC_DATABASE_URL \
    REDIS_URL COST_GUARD_REDIS_URL INTEGRATION_REDIS_URL
TEST_DB_URL_VARS := INTEGRATION_DATABASE_URL TASK16_DATABASE_URL TASK22_DATABASE_URL \
    TASK24_DATABASE_URL INCIDENT_TEST_DATABASE_URL OPERATIONS_TEST_DATABASE_URL \
    OPERATION_RUN_SIGNAL_DATABASE_URL OPERATION_RUN_SIGNAL_SYNC_DATABASE_URL \
    OPERATION_RUNS_DATABASE_URL OPERATION_RUN_TRANSITIONS_DATABASE_URL \
    OPERATION_RUN_CONCURRENCY_DATABASE_URL NOTIFICATION_OUTBOX_DATABASE_URL \
    ONBOARDING_PROJECTOR_DATABASE_URL CONTENT_PUBLISH_RECOVERY_DATABASE_URL \
    TASK13_DATABASE_URL TASK18_DATABASE_URL TASK19_ASYNC_DATABASE_URL \
    TASK19_SYNC_DATABASE_URL TASK20_DATABASE_URL \
    MIGRATION_UPGRADE_DATABASE_URL REDELIVERY_TEST_SYNC_DATABASE_URL

test-backend-local: db-budget-guard
	@missing=""; \
	for var in $(TEST_APP_URL_VARS) $(TEST_DB_URL_VARS); do \
		[ -n "$$(printenv $$var)" ] || missing="$$missing $$var"; \
	done; \
	if [ -n "$$missing" ]; then \
		echo "backend 테스트 URL은 기본값 없이 export해야 한다 (ci.yml backend 잡 env 참고). 빠진 변수:"; \
		for var in $$missing; do echo "  $$var"; done; \
		exit 1; \
	fi
	backend/.venv/bin/python -m ruff check backend
	cd backend && .venv/bin/python -m pytest

# Cloud SQL 연결 예산 불변식 가드 (config.py 풀 × terraform 인스턴스/CELERY_CONCURRENCY
# 합계 ≤ max_connections × 0.9). 어느 한쪽만 상향하면 여기서 배포 전에 잡힌다.
db-budget-guard:
	python3 scripts/check_db_connection_budget.py

test-frontend:
	cd site && npm test
	cd site && npm run lint
	cd site && npm run typecheck
	cd admin && npm test
	cd admin && npm run lint
	cd admin && npm run typecheck

build-frontend:
	cd site && npm run build
	cd admin && npm run build

demo-seed:
	docker compose exec api python -m app.utils.demo_seed

essence-backfill:
	docker compose exec api python -m app.utils.essence_backfill

# Admin 콘솔 첫 운영자(OWNER) 계정 생성/회전 — admin_users가 0명이면 프로덕션 로그인 불가(AUTH-4).
admin-create-owner:
	@read -p "Admin email: " email; \
	read -s -p "Password (min 14 chars): " pw; echo; \
	read -p "Name [Owner]: " name; \
	docker compose exec -e ADMIN_EMAIL="$$email" -e ADMIN_PASSWORD="$$pw" -e ADMIN_NAME="$${name:-Owner}" \
		api python -m app.utils.admin_user create-owner

copy-guard:
	python3 scripts/check_user_facing_terms.py

# 운영 중인 병원의 공개 표면 시각 승인(로고·대표색·카피·접근 유형) 상태 점검.
# 사진은 필수가 아니므로 판정에 넣지 않는다.
clinic-visual-readiness:
	python3 scripts/check_clinic_visual_readiness.py

# 위 점검에서 비어 있던 항목 중 근거가 확인된 값만 채운다. 먼저 dry run으로 확인한다.
clinic-visual-seed-report:
	docker compose exec api python -m app.utils.seed_clinic_visual_identity

clinic-visual-seed:
	docker compose exec api python -m app.utils.seed_clinic_visual_identity --apply

# ── 수동 태스크 실행 ───────────────────────────────────────────────
v0:
	@read -p "Hospital ID: " id; \
	docker compose exec worker celery -A app.core.celery_app call \
		app.workers.tasks.trigger_v0_report --args "[\"$$id\"]"

build-site:
	@read -p "Hospital ID: " id; \
	docker compose exec worker celery -A app.core.celery_app call \
		app.workers.tasks.build_aeo_site --args "[\"$$id\"]"

gen-content-now:
	docker compose exec worker celery -A app.core.celery_app call \
		app.workers.tasks.nightly_content_generation

monthly-report:
	docker compose exec worker celery -A app.core.celery_app call \
		app.workers.tasks.run_monthly_reports

# ── GCP 배포 ───────────────────────────────────────────────────────
setup-gcp:
	bash scripts/setup-gcp.sh

# 주의: $(VAR:-default)는 쉘 문법이라 Make 변수 안에서는 빈 값으로 풀린다 —
# Make 기본값은 $(or $(VAR),default) 를 사용한다.
build-image:
	docker build --platform linux/amd64 \
		-t "$(or $(GCP_REGION),asia-northeast3)-docker.pkg.dev/$(GCP_PROJECT_ID)/$(or $(GCP_ARTIFACT_REPO),reputation)/reputation:$(shell date +%Y%m%d-%H%M%S)" \
		-f backend/Dockerfile backend

deploy-api:
	bash scripts/deploy.sh api

deploy-worker:
	bash scripts/deploy.sh worker

deploy-beat:
	bash scripts/deploy.sh beat

deploy-all:
	bash scripts/deploy.sh all

deploy-migrate:
	bash scripts/deploy.sh migrate
