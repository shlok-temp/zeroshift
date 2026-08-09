"""Translate a docker-compose file into a Zerops service graph."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.catalog import (
    DOCKER_FALLBACK,
    NO_MANAGED_EQUIVALENT,
    MappedService,
    Note,
    infer_runtime,
    map_image,
)

HOSTNAME_CHARS = re.compile(r"[^a-z0-9]")
SECRET_HINT = re.compile(r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|credential|auth)", re.I)
MIN_PORT, MAX_PORT = 10, 65435
WEB_PORTS = {80, 443, 3000, 5000, 8000, 8080}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
GENERATED_SECRET = "<@generateRandomString(<32>)>"


class TranslationError(Exception):
    """The input could not be read as a docker-compose file."""


@dataclass
class Translation:
    services: list[MappedService] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    project_name: str = "migrated"

    @property
    def all_notes(self) -> list[Note]:
        combined = list(self.notes)
        for service in self.services:
            for note in service.notes:
                combined.append(Note(note.severity, service.hostname, note.title, note.detail))
        return sorted(combined, key=lambda n: SEVERITY_ORDER.get(n.severity, 3))

    @property
    def has_errors(self) -> bool:
        return any(n.severity == "error" for n in self.all_notes)


def sanitize_hostname(name: str, taken: set[str]) -> tuple[str, bool]:
    """Zerops hostnames allow only lowercase letters and digits, max 25 chars."""
    cleaned = HOSTNAME_CHARS.sub("", name.lower())[:25] or "service"
    if cleaned[0].isdigit():
        cleaned = ("s" + cleaned)[:25]
    changed = cleaned != name
    candidate, suffix = cleaned, 2
    while candidate in taken:
        tail = str(suffix)
        candidate = cleaned[: 25 - len(tail)] + tail
        suffix += 1
        changed = True
    taken.add(candidate)
    return candidate, changed


def parse_ports(raw: Any) -> tuple[list[int], list[Note]]:
    """Read container ports from compose short or long port syntax."""
    ports: list[int] = []
    notes: list[Note] = []
    for entry in raw or []:
        target = None
        if isinstance(entry, dict):
            target = entry.get("target")
        elif isinstance(entry, (str, int)):
            text = str(entry).split("/", 1)[0]
            try:
                target = int(text.split(":")[-1].split("-")[0])
            except ValueError:
                continue
        if target is None:
            continue
        port = int(target)
        if not MIN_PORT <= port <= MAX_PORT:
            notes.append(Note(
                "warning", "", f"Port {port} is outside the allowed range",
                f"Zerops requires ports between {MIN_PORT} and {MAX_PORT}. Move the "
                "service to a port in that range.",
            ))
        elif port not in ports:
            ports.append(port)
    return ports, notes


def parse_environment(service: dict) -> tuple[dict[str, str], dict[str, str], list[Note]]:
    """Split compose environment into plain variables and likely secrets."""
    plain: dict[str, str] = {}
    secrets: dict[str, str] = {}
    notes: list[Note] = []
    raw = service.get("environment")

    pairs: list[tuple[str, str]] = []
    if isinstance(raw, dict):
        pairs = [(k, "" if v is None else str(v)) for k, v in raw.items()]
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str):
                key, _, value = entry.partition("=")
                pairs.append((key.strip(), value.strip()))

    for key, value in pairs:
        if key:
            target = secrets if SECRET_HINT.search(key) else plain
            target[key] = value

    if service.get("env_file"):
        notes.append(Note(
            "info", "", "env_file is not carried over",
            "Values in env_file are not readable from the compose file alone. Add them "
            "as env secrets in the Zerops GUI, or paste them into dotEnvSecrets in the "
            "generated import file.",
        ))
    return plain, secrets, notes


def parse_volumes(service: dict) -> list[Note]:
    notes: list[Note] = []
    for volume in service.get("volumes") or []:
        if isinstance(volume, dict):
            source, target = volume.get("source", ""), volume.get("target", "")
        else:
            parts = str(volume).split(":")
            source = parts[0]
            target = parts[1] if len(parts) > 1 else ""

        if isinstance(source, str) and source.startswith((".", "/")):
            notes.append(Note(
                "warning", "", "Bind mount needs replacing",
                f"'{source}' is a host bind mount, and Zerops containers have no host "
                f"filesystem. Ship code through the build pipeline instead. For "
                f"persistent data at '{target or source}', attach a shared-storage "
                "service or move it to object storage.",
            ))
        else:
            notes.append(Note(
                "info", "", "Named volume becomes shared storage",
                f"Named volume '{source}' maps to a shared-storage service mounted into "
                "the runtime. Managed databases handle their own persistence, so drop "
                "the volume if it only backed a database.",
            ))
    return notes


def apply_secret_policy(service: MappedService) -> None:
    """Never carry compose secret values into a Zerops config."""
    if service.kind in ("database", "storage"):
        if service.secrets or service.env:
            service.notes.append(Note(
                "info", "", "Credentials are managed by Zerops",
                "Environment variables from compose were dropped. Zerops provisions this "
                "service and generates its own credentials, exposing them to other "
                "services as connection variables.",
            ))
        service.secrets = {}
        service.env = {}
        return

    if service.secrets:
        names = ", ".join(sorted(service.secrets))
        service.notes.append(Note(
            "warning", "", "Secret values replaced with generated ones",
            f"{names} looked like secrets, so their compose values were not copied. "
            "Each is set to a freshly generated 32-character string on import. If a "
            "value must match an external system, set it in the Zerops GUI instead.",
        ))
        service.secrets = {key: GENERATED_SECRET for key in service.secrets}


def read_command(service: dict) -> str | None:
    raw = service.get("command")
    if not raw:
        return None
    return raw if isinstance(raw, str) else " ".join(str(part) for part in raw)


def build_context_of(build: Any) -> str:
    if isinstance(build, dict):
        return str(build.get("context", "."))
    return str(build or ".")


def image_hint_from_build(build: Any) -> str:
    if isinstance(build, dict):
        return str(build.get("dockerfile") or build.get("context") or "")
    return str(build or "")


def translate(compose_text: str, project_name: str = "migrated", ha: bool = False) -> Translation:
    try:
        document = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise TranslationError(f"Invalid YAML: {exc}") from exc

    if not isinstance(document, dict):
        raise TranslationError("A compose file must be a YAML mapping at the top level.")
    services = document.get("services")
    if not isinstance(services, dict) or not services:
        raise TranslationError("No 'services:' section found in the compose file.")

    result = Translation(project_name=project_name)
    if "version" in document:
        result.notes.append(Note(
            "info", "", "Compose 'version' key ignored",
            "The version field is obsolete in modern Compose and has no Zerops equivalent.",
        ))

    taken: set[str] = set()
    dependencies: dict[str, list[str]] = {}
    renamed_to: dict[str, str] = {}

    for original, body in services.items():
        if not isinstance(body, dict):
            body = {}
        hostname, was_renamed = sanitize_hostname(str(original), taken)
        renamed_to[str(original)] = hostname
        notes: list[Note] = []

        if was_renamed:
            notes.append(Note(
                "info", "", f"Renamed '{original}' to '{hostname}'",
                "Zerops hostnames allow only lowercase letters and digits, up to 25 "
                "characters. Update any internal URLs that referenced the old name.",
            ))

        ports, port_notes = parse_ports(body.get("ports"))
        notes.extend(port_notes)
        exposed, _ = parse_ports(body.get("expose"))
        ports.extend(port for port in exposed if port not in ports)

        plain_env, secret_env, env_notes = parse_environment(body)
        notes.extend(env_notes)
        notes.extend(parse_volumes(body))

        raw_dependencies = body.get("depends_on")
        if isinstance(raw_dependencies, dict):
            dependencies[hostname] = [str(name) for name in raw_dependencies]
        elif isinstance(raw_dependencies, list):
            dependencies[hostname] = [str(name) for name in raw_dependencies]

        image = str(body.get("image") or "")
        build = body.get("build")
        command = read_command(body)
        short = image.split(":")[0].rsplit("/", 1)[-1].lower()
        managed = map_image(image, ha=ha) if image else None

        if build is not None:
            base, runtime_notes = infer_runtime(image or image_hint_from_build(build), command)
            notes.extend(runtime_notes)
            service = MappedService(
                hostname=hostname,
                type=base,
                kind="docker" if base == DOCKER_FALLBACK else "runtime",
                source_image=image or None,
                ports=ports,
                env=plain_env,
                build_context=build_context_of(build),
                notes=notes,
            )
        elif managed:
            service_type, kind, mapping_notes = managed
            notes.extend(mapping_notes)
            service = MappedService(
                hostname=hostname, type=service_type, kind=kind, source_image=image,
                ports=ports, env=plain_env, notes=notes,
            )
        elif short in NO_MANAGED_EQUIVALENT:
            label, guidance = NO_MANAGED_EQUIVALENT[short]
            notes.append(Note("error", "", f"No managed {label} on Zerops", guidance))
            service = MappedService(
                hostname=hostname, type=DOCKER_FALLBACK, kind="docker", source_image=image,
                ports=ports, env=plain_env, notes=notes,
            )
        else:
            base, runtime_notes = infer_runtime(image, command)
            notes.extend(runtime_notes)
            service = MappedService(
                hostname=hostname,
                type=base,
                kind="docker" if base == DOCKER_FALLBACK else "runtime",
                source_image=image,
                ports=ports,
                env=plain_env,
                notes=notes,
            )

        service.secrets = secret_env
        service.command = command
        service.depends_on = dependencies.get(hostname, [])
        apply_secret_policy(service)
        result.services.append(service)

    assign_priorities(result.services, dependencies, renamed_to)
    resolve_dependency_names(result.services, renamed_to)
    assign_public_service(result)
    check_unsupported_sections(document, result)
    return result


def assign_priorities(services: list[MappedService], dependencies: dict[str, list[str]],
                      renamed_to: dict[str, str]) -> None:
    """Higher priority is created first, so dependencies start before dependents."""
    by_hostname = {service.hostname: service for service in services}
    for service in services:
        service.priority = 10 if service.kind in ("database", "storage") else 1

    for hostname, needed in dependencies.items():
        dependent = by_hostname.get(hostname)
        if not dependent:
            continue
        for name in needed:
            provider = by_hostname.get(renamed_to.get(name, name))
            if provider and provider.priority <= dependent.priority:
                provider.priority = dependent.priority + 1


def resolve_dependency_names(services: list[MappedService],
                             renamed_to: dict[str, str]) -> None:
    """Rewrite depends_on to the sanitized hostnames services actually get."""
    for service in services:
        service.depends_on = [renamed_to.get(name, name) for name in service.depends_on]


def assign_public_service(result: Translation) -> None:
    """Expose exactly one service publicly; everything else stays private."""
    candidates = [s for s in result.services if s.ports]
    if not candidates:
        return

    web_facing = [s for s in candidates if any(p in WEB_PORTS for p in s.ports)]
    app_services = [s for s in (web_facing or candidates) if s.kind != "database"]
    if not app_services:
        return
    chosen = app_services[0]
    chosen.public = True
    chosen.notes.append(Note(
        "info", "", "Public access must be enabled after import",
        "During dogfooding, enableSubdomainAccess in the import file did not activate "
        "the subdomain by itself — the service returned 502 until public access was "
        "turned on. After importing, run: zcli service enable-subdomain <service-id>",
    ))

    if len(app_services) > 1:
        others = ", ".join(s.hostname for s in app_services if s is not chosen)
        result.notes.append(Note(
            "info", chosen.hostname, "Public access granted to one service",
            f"'{chosen.hostname}' received a public subdomain. {others} stay on the "
            "private network and remain reachable from other services by hostname. "
            "Enable subdomain access elsewhere only where you genuinely need it.",
        ))

    for service in result.services:
        if service.kind == "database" and service.ports:
            service.notes.append(Note(
                "info", "", "Database ports are not published",
                "Zerops keeps managed databases on the private network, so published "
                "ports from compose are dropped. Connect through the generated "
                "connection variables instead.",
            ))


def check_unsupported_sections(document: dict, result: Translation) -> None:
    networks = document.get("networks")
    if isinstance(networks, dict) and len(networks) > 1:
        result.notes.append(Note(
            "info", "", "Multiple networks collapse into one",
            "Every Zerops project has a single private network where services reach each "
            "other by hostname. Compose network segmentation is not reproduced, so "
            "enforce any isolation in your application layer.",
        ))

    for section in ("configs", "secrets"):
        if document.get(section):
            result.notes.append(Note(
                "warning", "", f"Top-level '{section}' not translated",
                f"Compose '{section}' has no direct Zerops equivalent. Use env secrets, "
                "or ship the files through the build pipeline.",
            ))
