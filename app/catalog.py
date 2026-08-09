"""Zerops service catalog and Docker image mapping."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["error", "warning", "info"]

POSTGRES_VERSIONS = ["14", "16", "17", "18"]
PYTHON_RUNTIMES = ["3.11", "3.12", "3.14"]
NODE_RUNTIMES = ["20", "22", "24"]
VALKEY_VERSION = "7.2"
MARIADB_VERSION = "10.6"
DOCKER_FALLBACK = "alpine/docker@26"


@dataclass
class Note:
    severity: Severity
    service: str
    title: str
    detail: str


@dataclass
class MappedService:
    hostname: str
    type: str
    kind: Literal["runtime", "database", "storage", "static", "docker"]
    source_image: str | None = None
    ports: list[int] = field(default_factory=list)
    public: bool = False
    env: dict[str, str] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    priority: int = 0
    build_context: str | None = None
    command: str | None = None
    notes: list[Note] = field(default_factory=list)


def resolve_version(tag: str, allowed: list[str], product: str,
                    default: str) -> tuple[str, Note | None]:
    """Pick the closest supported version, and say so when it is not a match."""
    if not tag:
        return default, None
    match = re.match(r"^(\d+)(?:\.(\d+))?", tag)
    if not match:
        return default, None

    major = match.group(1)
    if major in allowed:
        return major, None
    if match.group(2) and f"{major}.{match.group(2)}" in allowed:
        return f"{major}.{match.group(2)}", None

    numeric = [v for v in allowed if v.replace(".", "").isdigit()]
    if not numeric:
        return default, None

    requested = float(f"{major}.{match.group(2)}" if match.group(2) else major)
    below = [v for v in numeric if float(v) <= requested]
    if below:
        chosen = max(below, key=float)
        return chosen, Note(
            "warning", "", f"{product} {tag} is not offered; using {chosen}",
            f"Zerops offers {product} {', '.join(allowed)}. Your compose file pins "
            f"{tag}, so the closest older version was chosen. Confirm your schema and "
            "queries behave the same.",
        )

    chosen = min(numeric, key=float)
    return chosen, Note(
        "error", "", f"{product} {tag} is too old for Zerops",
        f"Zerops only offers {product} {', '.join(allowed)}, all newer than the {tag} "
        f"you pinned. The generated config uses {chosen}, which is a major-version "
        "upgrade — review the upstream breaking changes and plan a data migration "
        "before deploying, or your application may fail against the new server.",
    )


def split_image(image: str) -> tuple[str, str]:
    """Split an image reference into its short name and tag."""
    name = image.strip().split("@", 1)[0]
    tag = ""
    if ":" in name:
        head, candidate = name.rsplit(":", 1)
        if "/" not in candidate:
            name, tag = head, candidate
    return name.lower().rsplit("/", 1)[-1], tag


def map_image(image: str, ha: bool = False) -> tuple[str, str, list[Note]] | None:
    """Map a Docker image to a managed Zerops service, or None if none exists."""
    if not image.strip():
        return None
    short, tag = split_image(image)
    mode = "ha" if ha else "single"
    notes: list[Note] = []

    if short in ("postgres", "postgresql", "pgvector"):
        version, version_note = resolve_version(tag, POSTGRES_VERSIONS, "PostgreSQL", "17")
        if version_note:
            notes.append(version_note)
        if short == "pgvector":
            notes.append(Note(
                "warning", "", "pgvector extension not guaranteed",
                "Managed PostgreSQL may not ship the pgvector extension. Consider the "
                "managed Qdrant service for vector search, or confirm availability "
                "before relying on it.",
            ))
        return f"postgresql:{mode}@{version}", "database", notes

    if short in ("redis", "valkey", "redis-stack", "redis-stack-server"):
        if short.startswith("redis"):
            notes.append(Note(
                "info", "", "Redis is served by Valkey",
                "Zerops provides Valkey, the open-source Redis fork. It speaks the Redis "
                "protocol, so standard clients work unchanged. Redis Stack modules "
                "(RediSearch, RedisJSON) are not included.",
            ))
        return f"valkey:{mode}@{VALKEY_VERSION}", "database", notes

    if short in ("mysql", "mariadb", "percona"):
        if short != "mariadb":
            notes.append(Note(
                "warning", "", f"{short} is mapped to MariaDB",
                "Zerops has no managed MySQL, so MariaDB 10.6 is the closest equivalent. "
                "It is wire-compatible for most workloads but differs in JSON functions, "
                "some CTE edge cases, and system tables. Verify your migrations and any "
                "vendor-specific SQL before cutting over.",
            ))
        return f"mariadb:{mode}@{MARIADB_VERSION}", "database", notes

    if short in ("elasticsearch", "opensearch"):
        version = "9.2" if tag.startswith("9") else "8.16"
        if short == "opensearch":
            notes.append(Note(
                "warning", "", "OpenSearch mapped to Elasticsearch",
                "The APIs overlap heavily, but security plugins and some aggregations "
                "differ between the two projects.",
            ))
        return f"elasticsearch:{mode}@{version}", "database", notes

    if short in ("kafka", "cp-kafka", "redpanda"):
        if short == "redpanda":
            notes.append(Note(
                "info", "", "Redpanda mapped to Kafka",
                "Redpanda is Kafka-API compatible, so existing clients should work "
                "against the managed Kafka service.",
            ))
        return f"kafka:{mode}@3.9", "database", notes

    if short in ("nats", "nats-streaming"):
        return f"nats:{mode}@{'2.12' if tag.startswith('2.12') else '2.10'}", "database", notes

    if short in ("clickhouse", "clickhouse-server"):
        return f"clickhouse:{mode}@25.3", "database", notes

    if short == "qdrant":
        return f"qdrant:{mode}@{'1.12' if tag.startswith('1.12') else '1.10'}", "database", notes

    if short == "meilisearch":
        version = "1.44" if tag.startswith("1.4") else "1.20" if tag.startswith("1.2") else "1.10"
        return f"meilisearch:single@{version}", "database", notes

    if short == "typesense":
        return f"typesense:{mode}@{'30.2' if tag.startswith('30') else '27.1'}", "database", notes

    if short in ("minio", "seaweedfs"):
        notes.append(Note(
            "info", "", "Object storage is S3-compatible",
            "Zerops object storage exposes an S3 API. Point your existing S3 client at "
            "the generated connection variables instead of a self-hosted endpoint.",
        ))
        return "object-storage", "storage", notes

    if short in ("nginx", "httpd", "caddy"):
        notes.append(Note(
            "info", "", f"{short} may not need its own service",
            "Zerops terminates TLS and routes public traffic for you. If this container "
            "only served static files or reverse-proxied, replace it with a static "
            "service or drop it and expose the application directly.",
        ))
        return "ubuntu/nginx@latest", "runtime", notes

    return None


IMAGE_HINTS: list[tuple[str, str]] = [
    (r"\bpython\b", "python"),
    (r"\bnode(js)?\b", "nodejs"),
    (r"\bgolang\b|\bgo\b", "go"),
    (r"\bruby\b", "ruby"),
    (r"\bphp\b", "php"),
    (r"\brust\b", "rust"),
    (r"\b(openjdk|eclipse-temurin|amazoncorretto)\b", "java"),
    (r"\b(dotnet|aspnet)\b", "dotnet"),
    (r"\bbun\b", "bun"),
    (r"\bdeno\b", "deno"),
    (r"\belixir\b", "elixir"),
]

COMMAND_HINTS: list[tuple[str, str]] = [
    (r"\b(uvicorn|gunicorn|hypercorn|flask|celery|python3?)\b|manage\.py", "python"),
    (r"\b(npm|yarn|pnpm|node|next|nest|vite|remix)\b", "nodejs"),
    (r"\bbun\b", "bun"),
    (r"\bdeno\b", "deno"),
    (r"\b(rails|bundle|puma|unicorn|ruby)\b", "ruby"),
    (r"\bcargo\b", "rust"),
    (r"\bdotnet\b", "dotnet"),
    (r"\b(java|gradle|mvn)\b", "java"),
    (r"\b(php|artisan|composer)\b", "php"),
    (r"\b(mix|iex)\b", "elixir"),
    (r"\bgo\s+(run|build)\b", "go"),
]

DEFAULT_BASE = {
    "python": "ubuntu/python@3.12",
    "nodejs": "ubuntu/nodejs@22",
    "bun": "ubuntu/bun@latest",
    "deno": "ubuntu/deno@latest",
    "ruby": "ubuntu/ruby@3.4",
    "rust": "ubuntu/rust@stable",
    "dotnet": "ubuntu/dotnet@9",
    "java": "ubuntu/java@21",
    "php": "ubuntu/php-nginx@8.4",
    "elixir": "ubuntu/elixir@latest",
    "go": "ubuntu/go@1",
}


def base_for_language(language: str, tag: str) -> tuple[str, Note | None]:
    if language == "python":
        base, note = resolve_version(tag, PYTHON_RUNTIMES, "Python", "3.12")
        return f"ubuntu/python@{base}", note
    if language == "nodejs":
        base, note = resolve_version(tag, NODE_RUNTIMES, "Node.js", "22")
        return f"ubuntu/nodejs@{base}", note
    return DEFAULT_BASE[language], None


def infer_runtime(image: str, command: str | None = None) -> tuple[str, list[Note]]:
    """Infer a runtime base from the image, falling back to the start command."""
    notes: list[Note] = []
    if image:
        short, tag = split_image(image)
        for pattern, language in IMAGE_HINTS:
            if re.search(pattern, short):
                base, version_note = base_for_language(language, tag)
                if version_note:
                    notes.append(version_note)
                return base, notes

    if command:
        for pattern, language in COMMAND_HINTS:
            if re.search(pattern, command.lower()):
                base, _ = base_for_language(language, "")
                notes.append(Note(
                    "info", "", "Runtime inferred from the start command",
                    f"There was no image tag to read, but the command "
                    f"'{command[:60]}' indicates {language}. Using {base} — change it "
                    "if your project pins a different version.",
                ))
                return base, notes

    notes.append(Note(
        "warning", "", "Runtime could not be inferred",
        f"Nothing in image '{image or '(none)'}' or the start command identified a "
        "language. Falling back to a Docker service, which runs your image as-is but "
        "gives up Zerops build caching and zero-downtime deploys. Set the runtime "
        "manually to get a native build.",
    ))
    return DOCKER_FALLBACK, notes


NO_MANAGED_EQUIVALENT = {
    "mongo": ("MongoDB",
              "Zerops has no managed MongoDB. Run it as a Docker service with a "
              "shared-storage mount for persistence, or use a hosted provider such as "
              "MongoDB Atlas reached over the public internet."),
    "mongodb": ("MongoDB",
                "Zerops has no managed MongoDB. Run it as a Docker service with a "
                "shared-storage mount, or use a hosted provider."),
    "rabbitmq": ("RabbitMQ",
                 "No managed RabbitMQ. Zerops offers managed NATS and Kafka for "
                 "messaging; NATS is the closest lightweight substitute. Otherwise run "
                 "RabbitMQ as a Docker service."),
    "memcached": ("Memcached",
                  "No managed Memcached. Valkey covers the same caching use case and is "
                  "managed, replicated, and backed up."),
    "cassandra": ("Cassandra",
                  "No managed Cassandra. Consider ClickHouse for analytical workloads, "
                  "or run Cassandra as a Docker service."),
    "influxdb": ("InfluxDB",
                 "No managed InfluxDB. ClickHouse handles most time-series workloads "
                 "well and is managed."),
    "neo4j": ("Neo4j", "No managed Neo4j. Run it as a Docker service with shared storage."),
    "couchdb": ("CouchDB", "No managed CouchDB. Consider PostgreSQL with JSONB columns."),
}
