.PHONY: setup up down logs migrate revision test demo-seed essence-backfill copy-guard admin-create-owner
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

test:
	docker compose exec api pytest -v

test-local: test-backend-local test-frontend copy-guard

test-backend-local:
	backend/.venv/bin/python -m ruff check backend
	backend/.venv/bin/python -m pytest

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
		-t "$(or $(GCP_REGION),us-central1)-docker.pkg.dev/$(GCP_PROJECT_ID)/$(or $(GCP_ARTIFACT_REPO),reputation)/reputation:$(shell date +%Y%m%d-%H%M%S)" \
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
