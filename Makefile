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
# 컨테이너 네트워크에서는 db:5432다. 기본값(localhost:5434)을 그대로 두면 컨테이너
# 안에서는 접속이 안 돼 그 테스트들이 전부 조용히 skip된다.
TEST_DB_PLAIN := postgresql://reputation:reputation@db:5432/reputation_test
TEST_DB_ASYNC := postgresql+asyncpg://reputation:reputation@db:5432/reputation_test
TEST_DB_SYNC  := postgresql+psycopg2://reputation:reputation@db:5432/reputation_test

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
# 읽는 계약 테스트가 FileNotFoundError로 깨지고, 테스트 DB URL을 env로 받지 않고
# localhost:5434를 하드코딩한 파일들(backend/tests에 26개)은 컨테이너 네트워크에서
# 접속하지 못한다. 둘 다 이 타깃보다 넓은 문제다 — 전체 스위트는 `make test-backend-local`
# (호스트 실행)이 정본이고, 이 타깃은 컨테이너 환경 자체를 검증하는 용도다.
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
		-e INTEGRATION_DATABASE_URL="$(TEST_DB_PLAIN)" \
		-e INCIDENT_TEST_DATABASE_URL="$(TEST_DB_ASYNC)" \
		-e OPERATIONS_TEST_DATABASE_URL="$(TEST_DB_SYNC)" \
		api uv run --no-sync pytest -v -p no:cacheprovider

test-local: test-backend-local test-frontend copy-guard

test-backend-local: db-budget-guard
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
