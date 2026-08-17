# Karbi

Internal automation platform for Wildberries workflows.

The project starts as a modular monolith with independently runnable API and worker processes. PostgreSQL schemas isolate platform data, Wildberries account/catalog data, and review aggregation data.

Runtime configuration is loaded from YAML. Use `backend/shared/settings/config.local.yaml` locally,
`config.docker.yaml` in Compose, and inject secrets through the `${ENVIRONMENT_VARIABLE}` placeholders.
Production secrets must never be committed.

The React frontend lives in `frontend/` and uses the FastAPI endpoints under `/api/v1`. Run it with
`just frontend-dev`; validate it with `just frontend-check`. Compose builds the frontend separately and
the edge Nginx serves it while forwarding `/api/` to FastAPI.

Creating a Wildberries seller stores its API key encrypted and writes a catalog-sync event to the
`wb_core.outbox_events` table in the same transaction. The standalone `outbox-publisher` process publishes
pending events to Kafka. The WB worker consumes `wb.catalog.sync.requested`, loads all product cards through
the official cursor-based Content API, and upserts them into `wb_core.articles`. Consumer-side inbox records
make repeated Kafka delivery safe. Deleting a seller physically removes the seller, credentials, products,
unpublished events, and review history.

Employee accounts are created from an application container or a configured local environment:

```bash
python -m backend.commands.create_user --username employee
```

Passwords are Argon2 hashes. Authentication uses a 24-hour access JWT kept in frontend memory and a
single-use, rotating 7-day refresh token in an HttpOnly cookie backed by Redis.

Shared `storage/pg` owns the SQLAlchemy engine and sessions. Each
`modules/<module>/infrastructure/postgres` package owns only that module's ORM models and repositories;
this keeps database adapters on the module boundary and makes later service extraction mechanical.
