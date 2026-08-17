set dotenv-load

compose := "docker compose -f deploy/compose.yaml"

default:
    @just --list

install:
    uv sync

format:
    uv run ruff format backend
    uv run ruff check backend --fix

lint:
    uv run ruff check backend
    uv run ruff format --check backend
    uv run mypy backend

migrate-platform:
    uv run alembic -n platform upgrade head

migrate-wb-core:
    uv run alembic -n wb_core upgrade head

migrate-wb-reviews:
    uv run alembic -n wb_reviews upgrade head

migrate-all: migrate-platform migrate-wb-core migrate-wb-reviews

compose-up:
    {{ compose }} up -d --build

compose-down:
    {{ compose }} down --remove-orphans

compose-status:
    {{ compose }} ps

compose-logs service="":
    {{ compose }} logs {{ service }}

