# Karbi

Internal automation platform for Wildberries workflows.

The project starts as a modular monolith with independently runnable API and worker processes. PostgreSQL schemas isolate platform data, Wildberries account/catalog data, and review aggregation data.

Runtime configuration is loaded from YAML. Use `backend/shared/settings/config.local.yaml` locally,
`config.docker.yaml` in Compose, and inject secrets through the `${ENVIRONMENT_VARIABLE}` placeholders.
Production secrets must never be committed.

The React frontend lives in `frontend/`. During the first UI iteration it uses MSW to serve deterministic
mock responses for login, automations, sellers, products, and daily review snapshots. Run it with
`just frontend-dev`; validate it with `just frontend-check`. Compose builds the frontend separately and
the edge Nginx serves it while forwarding `/api/` to FastAPI.

Shared `storage/pg` owns the SQLAlchemy engine and sessions. Each
`modules/<module>/infrastructure/postgres` package owns only that module's ORM models and repositories;
this keeps database adapters on the module boundary and makes later service extraction mechanical.
