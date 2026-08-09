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

## AI usage disclosure

Built with the help of [Claude Code](https://claude.com/claude-code), using Claude as a pair-programming and research assistant under my direction. The division of labour was:

**1. Ideation and target selection.**
I chose the hackathon and defined the core constraint: the project had to solve a problem relevant to Zerops itself, rather than being a generic application deployed on Zerops.

**2. Platform research and service discovery.**
I researched Zerops' platform, ran `zcli` commands, inspected the deployment behaviour, and worked directly with Zerops' published documentation and schemas. Claude assisted with parsing and organizing this information, including extracting the service catalogue from the official JSON Schema. The resulting service mapping was therefore grounded in Zerops' published schema rather than generated from model knowledge.

**3. Architecture decisions.**
The architecture was mine. Claude proposed an early five-service design, which I rejected because three services did not have meaningful responsibilities and would have looked like padding. I defined the leaner architecture and added services only when they became genuinely load-bearing.

**4. Implementation.**
I provided the initial project boilerplate, structure, requirements, and implementation direction. Claude Code then helped write and iterate on substantial portions of the Python code under my review. I established and enforced the coding standards throughout — including no underscore-prefixed function names, module-level imports, and comments only where the reasoning was not self-evident. Several generated implementations were rejected and rewritten to meet these requirements.

**5. Zerops integration and deployment.**
I performed the actual `zcli` setup and commands, authenticated against my Zerops account, imported the project, configured the deployment, and explicitly authorized commands affecting my machine or account. Claude assisted with command guidance and troubleshooting, but the deployment actions and account-level operations were performed by me.

**6. Debugging and validation.**
Debugging was collaborative, with both human review and Claude-assisted log analysis. I identified several important issues, including:

* PyYAML generating `&id`/`*id` anchors when services shared a build-command list.
* Database credentials being injected into every runtime, including a frontend that did not require database access.
* The three-service requirement on the submission page, which required an architectural change.
* A GitHub account mismatch before anything was pushed to the wrong repository.

Claude assisted in identifying additional issues from deployment logs, including the `uvicorn: command not found` failure caused by build and runtime using separate containers, and the `failureTimeout` type mismatch between the published schema and the live API.

**7. Documentation and commits.**
Claude drafted parts of the documentation and commit text, which I reviewed and corrected. I verified factual claims against the actual code and deployment configuration. This caught issues in an early README draft, including an incorrect runtime version, incorrect database version, a claimed SQLite fallback that was actually in-memory, and an incorrect service count.

**8. Demo and submission.**
I prepared and delivered the demo and completed the submission.

The project was therefore **human-directed and human-validated, with Claude Code used as an implementation and research assistant rather than as the project author**. All architectural decisions, `zcli` operations, deployment actions, validation, and final submission were under my control.


## Limits

Honest about scope. `deploy` and `x-` extension fields are ignored.
`healthcheck` blocks aren't parsed — a default HTTP check on the first port is
emitted instead. Multi-stage Dockerfiles aren't read; build commands come from
runtime conventions. Compose `profiles` and multiple networks collapse into
Zerops' single private network.
