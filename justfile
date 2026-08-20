set dotenv-load

compose := "docker compose -f deploy/compose.yaml"
prod_compose := "sudo docker compose --env-file .env -f deploy/compose.yaml"
test_compose := "docker compose -f deploy/compose.test.yaml"
test_config := "backend/shared/settings/config.test.yaml"

default:
    @just --list

install:
    uv sync
    cd frontend && npm ci

frontend-dev:
    cd frontend && npm run dev

frontend-check:
    cd frontend && npm run lint
    cd frontend && npm test
    cd frontend && npm run build

format:
    uv run ruff format backend
    uv run ruff check backend --fix

lint:
    uv run ruff check backend
    uv run ruff format --check backend
    uv run mypy backend

test:
    CONFIG_PATH={{ test_config }} uv run pytest

test-infra-up:
    {{ test_compose }} up -d
    @for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do if {{ test_compose }} exec -T db-test pg_isready -U karbi -d karbi_test >/dev/null 2>&1 && {{ test_compose }} exec -T redis-test redis-cli ping >/dev/null 2>&1; then exit 0; fi; sleep 1; done; {{ test_compose }} logs; exit 1

test-infra-down:
    {{ test_compose }} down --volumes --remove-orphans

test-migrate:
    DATABASE_URL=postgresql+asyncpg://karbi:karbi@localhost:55433/karbi_test uv run alembic -n platform upgrade head
    DATABASE_URL=postgresql+asyncpg://karbi:karbi@localhost:55433/karbi_test uv run alembic -n wb_core upgrade head
    DATABASE_URL=postgresql+asyncpg://karbi:karbi@localhost:55433/karbi_test uv run alembic -n wb_reviews upgrade head
    DATABASE_URL=postgresql+asyncpg://karbi:karbi@localhost:55433/karbi_test uv run alembic -n notifications upgrade head
    DATABASE_URL=postgresql+asyncpg://karbi:karbi@localhost:55433/karbi_test uv run alembic -n wb_turnover upgrade head

test-all: test-infra-up test-migrate
    CONFIG_PATH={{ test_config }} uv run pytest

migrate-platform:
    uv run alembic -n platform upgrade head

migrate-wb-core:
    uv run alembic -n wb_core upgrade head

migrate-wb-reviews:
    uv run alembic -n wb_reviews upgrade head

migrate-notifications:
    uv run alembic -n notifications upgrade head

migrate-wb-turnover:
    uv run alembic -n wb_turnover upgrade head

migrate-all: migrate-platform migrate-wb-core migrate-wb-reviews migrate-notifications migrate-wb-turnover

compose-up:
    {{ compose }} up -d --build

compose-down:
    {{ compose }} down --remove-orphans

compose-status:
    {{ compose }} ps

compose-logs service="":
    {{ compose }} logs {{ service }}

# Build and deploy the current server checkout, then verify critical processes.
prod-deploy:
    @test -f .env || (echo "Missing production .env in the repository root" >&2; exit 1)
    {{ prod_compose }} config --quiet
    {{ prod_compose }} build
    {{ prod_compose }} up -d --remove-orphans
    @for attempt in $(seq 1 36); do if curl --fail --silent --max-time 5 http://127.0.0.1:8080/api/v1/health/ready >/dev/null; then break; fi; if [ "$attempt" = 36 ]; then {{ prod_compose }} logs --tail=200 migrate api wb-reviews-worker outbox-publisher nginx; exit 1; fi; sleep 5; done
    @test "$({{ prod_compose }} ps --status running -q wb-reviews-worker | wc -l | tr -d ' ')" = "1"
    @test "$({{ prod_compose }} ps --status running -q outbox-publisher | wc -l | tr -d ' ')" = "1"
    @test "$({{ prod_compose }} ps --status running -q notifications-worker | wc -l | tr -d ' ')" = "1"
    @test "$({{ prod_compose }} ps --status running -q wb-turnover-worker | wc -l | tr -d ' ')" = "1"
    {{ prod_compose }} ps

# Start production containers without rebuilding images.
prod-up:
    @test -f .env || (echo "Missing production .env in the repository root" >&2; exit 1)
    {{ prod_compose }} up -d --remove-orphans

# Stop production containers without deleting persistent volumes.
prod-down:
    {{ prod_compose }} down --remove-orphans

# Show production container and health status.
prod-status:
    {{ prod_compose }} ps

# Follow production logs for all services or one named service.
prod-logs service="":
    {{ prod_compose }} logs --tail=200 --follow {{ service }}
