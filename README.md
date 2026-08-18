# Karbi

Karbi is an internal automation platform for marketplace workflows. The current application manages
Wildberries sellers, synchronizes their product catalogs, and stores daily review-count snapshots for every
product and rating from one to five stars.

The project is a modular monolith: business modules share one deployment and PostgreSQL instance, while API,
background consumers, and the outbox publisher run as independent processes. Module boundaries and database
schemas are kept explicit so a module can be extracted into a separate service later.

## Stack

- FastAPI, SQLAlchemy 2, Alembic, PostgreSQL
- Kafka with transactional outbox and idempotent consumers
- Redis for refresh sessions and sliding-window rate limiting
- React, TypeScript, TanStack Query, Vite
- Nginx as the single HTTP entry point
- Docker Compose for local and server deployment

## Runtime components

| Component | Responsibility |
| --- | --- |
| `api` | Authentication, seller management, review history, and manual synchronization endpoints |
| `wb-reviews-worker` | WB catalog/review consumers and the daily scheduler |
| `outbox-publisher` | Reliably publishes committed outbox events to Kafka |
| `frontend` | Employee web interface |
| `nginx` | Serves the frontend and proxies `/api/` to FastAPI |
| `db`, `redis`, `kafka` | Application infrastructure |

PostgreSQL data is separated into schemas:

- `platform` — employee accounts;
- `wb_core` — sellers, encrypted credentials, articles, inbox, and outbox;
- `wb_reviews` — daily rating snapshots and synchronization runs.

The review scheduler starts one run per day after 12:00 `Europe/Moscow`. A manual run uses the same
outbox/Kafka pipeline. WB requests are made only after the SQLAlchemy session has been closed; collected data
is persisted in a separate short transaction.

## Quick start

Requirements: Docker with Compose, or Python 3.12, `uv`, Node.js 22, and `just` for local development.

1. Create the environment file:

   ```bash
   cp .env.example .env
   ```

2. Generate secrets and place them in `.env`:

   ```bash
   uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   openssl rand -hex 32
   ```

   Use the Fernet value for `CREDENTIALS_ENCRYPTION_KEYS`. Generate separate random values for
   `CREDENTIALS_FINGERPRINT_KEY` and `JWT_SECRET`.

3. Start the application:

   ```bash
   just compose-up
   ```

   By default, the web interface is available at [http://localhost:8080](http://localhost:8080).
   Migrations run automatically before the API and workers start.

4. Create the first employee account:

   ```bash
   docker compose -f deploy/compose.yaml exec api \
     python -m backend.commands.create_user --username employee
   ```

Useful operational commands:

```bash
just compose-status
just compose-logs api
just compose-logs wb-reviews-worker
just compose-down
```

## Configuration and secrets

Runtime settings are loaded from YAML:

- `backend/shared/settings/config.local.yaml` for direct local execution;
- `backend/shared/settings/config.docker.yaml` for Compose;
- `backend/shared/settings/config.test.yaml` for tests.

Docker configuration resolves credentials from environment variables. Never commit `.env`, WB API keys, JWT
secrets, or credential-encryption keys. `APP_ENVIRONMENT=production` enables strict startup validation for
required secrets and unsafe CORS settings.

Wildberries API keys are encrypted before being stored. The first value in `CREDENTIALS_ENCRYPTION_KEYS` is
used for new writes; additional comma-separated keys can be retained temporarily during key rotation.

## Development

Install dependencies:

```bash
just install
```

Run the API and worker directly using `CONFIG_PATH` when needed. The frontend development server starts with:

```bash
just frontend-dev
```

Quality checks:

```bash
just lint
just frontend-check
```

Run backend tests with isolated PostgreSQL and Redis:

```bash
just test-infra-up
just test-migrate
just test
just test-infra-down
```

Apply all local migrations:

```bash
just migrate-all
```

Kafka topics are registered in application code and created through the Kafka admin client at startup. They
must not be created by ad-hoc shell scripts.

## Deployment

Every push to `main` starts the CI/CD orchestrator. The tested commit is then deployed over SSH to the
dedicated checkout at `/opt/karbi/app`: the server fetches that exact revision, builds the Docker images
locally, applies migrations through Compose, starts the stack, and verifies the API and background workers.
No container registry is required.

The production `.env` remains on the server and is never copied from CI. The repository must define the
`SSH_HOST`, `SSH_USERNAME`, `SSH_PORT`, `SSH_PRIVATE_KEY`, and `SSH_FINGERPRINT` Actions secrets.

## License

Karbi is available under the [MIT License](LICENSE).
