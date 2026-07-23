.PHONY: up down logs test prove seed reset shell psql

up:            ## Build and start everything (first run seeds the database)
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f api

test:          ## 70 tests against real Postgres with real policies
	docker compose exec api pytest

prove:         ## Readable walk through the five edge cases
	docker compose exec api python -m scripts.prove_isolation

seed:
	docker compose exec api python -m app.seed

reset:         ## Wipe the database and start over
	docker compose down -v
	docker compose up --build

shell:
	docker compose exec api bash

psql:          ## Connect as the UNPRIVILEGED app role, to poke at RLS by hand
	docker compose exec db psql "postgresql://agencydesk_app:app_pw@localhost/agencydesk"
