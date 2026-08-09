# Bug report: `zerops.yaml` health-check timers — published JSON Schema and docs say `integer`, deploy API requires a Go duration string

## Summary

The official `zerops.yaml` JSON Schema types `healthCheck.failureTimeout` (and the
other timer fields) as `"type": "integer"`, and the documentation shows bare
integers in every example. The deploy API rejects a bare integer and requires a
Go duration string such as `"60s"`.

A `zerops.yaml` that validates cleanly against Zerops' own published schema is
therefore rejected at deploy time. Editor tooling reports the file as valid,
because the schema is registered with SchemaStore and auto-applies to files
named `zerops.yaml`, so the first sign of a problem is a failed pipeline.

## Environment

| | |
|---|---|
| zcli | v1.1.0 (go1.26.3) windows/amd64 |
| Region | prg1 |
| Observed | 2026-08-09 |
| Service type | `ubuntu/python@3.12` |

## Reproduction

**1. Write a `zerops.yaml` following the documented example.**

```yaml
zerops:
  - setup: api
    build:
      base: ubuntu/python@3.12
      buildCommands:
        - pip install -r requirements.txt
      deployFiles:
        - ./
    run:
      base: ubuntu/python@3.12
      ports:
        - port: 8000
          httpSupport: true
      start: uvicorn app.main:app --host 0.0.0.0 --port 8000
      healthCheck:
        httpGet:
          port: 8000
          path: /health
        failureTimeout: 60
```

**2. Confirm it validates against the published schema.**

```bash
curl -sO https://api.app-prg1.zerops.io/api/rest/public/settings/zerops-yml-json-schema.json

python - <<'PY'
import json, yaml
from jsonschema import Draft7Validator
schema = json.load(open("zerops-yml-json-schema.json"))
document = yaml.safe_load(open("zerops.yaml"))
errors = list(Draft7Validator(schema).iter_errors(document))
print("schema errors:", len(errors))
PY
```

Output:

```
schema errors: 0
```

**3. Deploy it.**

```bash
zcli push api --setup api
```

Result:

```
✗ ERR Invalid YAML file.
✗ ERR - code: yamlValidationInvalidYaml
✗ ERR   error: Invalid YAML file.
✗ ERR   reason:
✗ ERR       - |-
✗ ERR         yaml: unmarshal errors:
✗ ERR           line 18: cannot unmarshal !!int `60` into time.Duration
```

## Expected vs actual

**Expected:** a document that validates against the published schema is accepted
by the deploy API.

**Actual:** the deploy API unmarshals these fields into `time.Duration` and
rejects the integer the schema mandates.

## Workaround

Quote the value as a Go duration:

```yaml
healthCheck:
  httpGet:
    port: 8000
    path: /health
  failureTimeout: 60s
```

This deploys successfully. It then fails schema validation, so any tooling that
validates `zerops.yaml` has to special-case these fields.

## Affected fields

All five timer fields share the shape, though only `run.healthCheck.failureTimeout`
was confirmed against the deploy API. The rest are inferred from carrying the
same `"type": "integer"` declaration in the schema and the same duration
semantics.

| Field | Schema path | Schema type | Confirmed |
|---|---|---|---|
| `failureTimeout` | `run.healthCheck` | `integer` | yes |
| `disconnectTimeout` | `run.healthCheck` | `integer` | not tested |
| `recoveryTimeout` | `run.healthCheck` | `integer` | not tested |
| `execPeriod` | `run.healthCheck` | `integer` | not tested |
| `failureTimeout` | `deploy.readinessCheck` | `integer` | not tested |
| `retryPeriod` | `deploy.readinessCheck` | `integer` | not tested |

## Sources

Schema, `properties.zerops.items.properties.run.properties.healthCheck`:

```json
"failureTimeout": {
  "type": "integer",
  "description": "Time until container fails after consecutive health check failures (reset by success)."
}
```

- Schema: <https://api.app-prg1.zerops.io/api/rest/public/settings/zerops-yml-json-schema.json>
- Docs: <https://docs.zerops.io/zerops-yaml/specification> — every `healthCheck`
  and `readinessCheck` example uses bare integers (`failureTimeout: 60`,
  `execPeriod: 10`), and the prose describes them as "time in seconds", which
  reinforces the integer reading.

## Suggested fix

Either accept both forms at the API and keep the schema as-is, or change the
schema and docs to a string with a duration pattern. The second is clearer about
intent, but would invalidate existing working configs that use integers if the
API ever stopped accepting them — so accepting both seems safer.

Worth noting the docs and schema currently agree with each other and disagree
with the API, so a docs-only change would leave the schema wrong.

## Why this is easy to miss

The schema is registered with SchemaStore, so editors apply it automatically to
`zerops.yaml`. A developer following the documented example gets green
validation in-editor and a red pipeline on deploy, with an error mentioning
`time.Duration` — a Go internal type that does not appear anywhere in the
documentation.
