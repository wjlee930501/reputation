.PHONY: setup up down logs migrate revision test

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
