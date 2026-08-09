# ZeroShift

Paste a `docker-compose.yml`, get the two files Zerops needs to run your stack —
plus an honest list of everything that won't survive the move.

**Live:** https://api-2d7f-8000.prg1.zerops.app

Built for the [WeMakeDevs Zerops hackathon](https://www.wemakedevs.org/hackathons/zerops).

## The problem

Zerops doesn't run `docker-compose`. It runs *managed services* — a real
PostgreSQL server it operates for you, not a container you babysit. That's the
selling point, and it's also the migration wall: your compose file describes
containers, ports, and bind mounts, and none of those concepts survive the
translation intact.

So you rewrite the config by hand, guessing. The guesses that bite:

- `mysql:8` has no managed equivalent. You get MariaDB, which is *not* MySQL.
- `redis:7` becomes Valkey. Protocol-compatible, but Redis Stack modules are gone.
- `mongo` has no managed service at all. Nothing warns you.
- `postgres:15` isn't offered — 14, 16, 17, 18 are. Picking wrong means a
  version jump you didn't plan.
- Bind mounts assume a host filesystem. Zerops containers have none.
- Build and run happen in **separate containers**. Anything you `pip install`
  during build is gone at runtime unless you say so explicitly.

Every one of those is a failed deploy and a confused half hour. ZeroShift finds
them before you push.

## What it produces

**`zerops-project-import.yml`** — the topology. Which services exist, their
managed types, ordering via `priority`, and generated secrets using
`<@generateRandomString(<32>)>`.

**`zerops.yaml`** — the pipeline. Build commands, deploy files, start command,
health checks, and the env vars that wire your app to its database.

**A diagnostics report** — errors, warnings, and notes, worst first. This is the
part that matters. Anyone can emit YAML; the value is knowing that your MySQL
isn't MySQL anymore.

## Worked example

Input — a React frontend, a Node API, and Postgres:

```yaml
services:
  frontend:
    build: ./frontend
    image: node:20
    ports: ["3000:3000"]
    volumes: ["./frontend/src:/app/src"]
  api:
    build: ./api
    image: node:20
    ports: ["4000:4000"]
    command: node server.js
    depends_on: [db]
  db:
    image: postgres:15
```

What comes back:

| Service | Compose | Zerops | Public |
|---|---|---|---|
| `frontend` | `build: ./frontend` | `ubuntu/nodejs@20` | yes |
| `api` | `build: ./api` | `ubuntu/nodejs@20` | no |
| `db` | `postgres:15` | `postgresql:single@14` | no |

And four findings worth reading:

- **warning** — Postgres 15 isn't offered; 14 was chosen as the closest older
  version. Verify your schema before deploying.
- **warning** — `./frontend/src` is a bind mount. Containers here are ephemeral
  with no host filesystem; deploy code through the build pipeline instead.
- **warning** — `JWT_SECRET: dev-secret-123` was replaced with a generated
  value. A committed dev secret shouldn't become a production one.
- **info** — `frontend` was picked for public access; `api` stays on the private
  network, reachable at `http://api:4000` by hostname.

Note what `api` gets that `frontend` doesn't: database credentials. Only
services that declared `depends_on: [db]` receive them. A frontend that never
talks to the database has no business holding its password.

## How the mapping was derived

Not from memory. Zerops publishes JSON Schemas for both file formats, and the
import schema contains an `enum` of every valid service type string. That file
is committed under `schemas/` and is the single source of truth: the catalog in
`app/catalog.py` is written against it, and every generated file is validated
back against it before you see it.

That's why "there is no managed MongoDB" is a fact here rather than a guess —
it's absent from an enum that lists 202 valid service types.

## Architecture

Two services on Zerops:

```
Internet → api (ubuntu/python@3.12, public)  →  db (postgresql:single@17, private)
```

The API is FastAPI with server-rendered Jinja templates. Postgres stores shared
migrations so a result gets a URL you can send someone. Credentials arrive as
`${db_password}` — a reference the platform resolves at deploy time, never a
literal in the repo.

`zerops.yaml` and `zerops-project-import.yml` in this repo are byte-for-byte
what ZeroShift generates from this project's own `docker-compose.yml`. The tool
deployed itself.

## Running locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Without `DB_HOST` set it falls back to an in-memory store, so share links work
offline but reset when the process restarts.

```bash
python -m pytest -q     # 23 tests
```

## API

```bash
curl -X POST https://api-2d7f-8000.prg1.zerops.app/api/translate \
  -H 'Content-Type: application/json' \
  -d '{"compose": "services:\n  db:\n    image: mysql:8\n"}'
```

Returns services, diagnostics, and both rendered files as JSON.

## Two things Zerops taught me

**Build and run are different containers.** The first deploy died with
`uvicorn: command not found`. Installing during `build` doesn't help — that
filesystem is discarded. Dependencies belong in `run.prepareCommands`, with
`build.addToRunPrepare` passing the manifest across. This was a bug in the
generator, not just my deploy: every Python config it emitted was broken until
I fixed it.

**`enableSubdomainAccess: true` in the import file didn't enable public
access.** The service returned 502 until `zcli service enable-subdomain`. The
key is accepted and silently insufficient, so the generated file now carries a
note telling you to run that command.

## Limits

Honest about scope. `deploy` and `x-` extension fields are ignored.
`healthcheck` blocks aren't parsed — a default HTTP check on the first port is
emitted instead. Multi-stage Dockerfiles aren't read; build commands come from
runtime conventions. Compose `profiles` and multiple networks collapse into
Zerops' single private network.
