import pytest

from app.emitters import import_document, render_import_yaml, zerops_document
from app.translate import TranslationError, sanitize_hostname, translate
from app.validate import validate_import, validate_zerops_yaml


def note_titles(translation):
    return [n.title for n in translation.all_notes]


def find(translation, hostname):
    return next(s for s in translation.services if s.hostname == hostname)


def test_postgres_maps_to_managed_service():
    tr = translate("services:\n  db:\n    image: postgres:16\n")
    assert find(tr, "db").type == "postgresql:single@16"
    assert find(tr, "db").kind == "database"


def test_redis_maps_to_valkey_with_warning():
    tr = translate("services:\n  cache:\n    image: redis:7\n")
    assert find(tr, "cache").type == "valkey:single@7.2"
    assert any("Valkey" in t for t in note_titles(tr))


def test_mysql_warns_about_mariadb_substitution():
    tr = translate("services:\n  db:\n    image: mysql:8\n")
    assert find(tr, "db").type.startswith("mariadb")
    assert any("MariaDB" in t for t in note_titles(tr))


def test_mongo_has_no_managed_equivalent():
    tr = translate("services:\n  m:\n    image: mongo:7\n")
    assert any(n.severity == "error" for n in tr.all_notes)
    assert find(tr, "m").kind == "docker"


def test_too_old_version_reports_error_and_picks_closest():
    tr = translate("services:\n  db:\n    image: postgres:13\n")
    assert tr.services[0].type == "postgresql:single@14"
    assert any(n.severity == "error" and "too old" in n.title for n in tr.all_notes)


def test_unavailable_middle_version_downgrades_with_warning():
    tr = translate("services:\n  db:\n    image: postgres:15\n")
    assert tr.services[0].type == "postgresql:single@14"
    assert any(n.severity == "warning" for n in tr.all_notes)


def test_unpinned_version_uses_conservative_default():
    tr = translate("services:\n  db:\n    image: postgres\n")
    assert tr.services[0].type == "postgresql:single@17"
    assert not any(n.severity == "error" for n in tr.all_notes)


def test_hostname_is_sanitized():
    taken = set()
    assert sanitize_hostname("My_Web_App", taken)[0] == "mywebapp"
    assert sanitize_hostname("9lives", set())[0] == "s9lives"
    assert len(sanitize_hostname("x" * 40, set())[0]) == 25


def test_duplicate_hostnames_are_made_unique():
    taken = set()
    first, _ = sanitize_hostname("web-1", taken)
    second, _ = sanitize_hostname("web_1", taken)
    assert first != second


def test_runtime_inferred_from_start_command():
    tr = translate(
        "services:\n  web:\n    build: .\n"
        "    command: uvicorn main:app --host 0.0.0.0\n"
    )
    assert find(tr, "web").type == "ubuntu/python@3.12"


def test_secrets_are_not_copied_verbatim():
    tr = translate(
        "services:\n  web:\n    build: .\n"
        "    environment:\n      SECRET_KEY: hunter2\n      LOG_LEVEL: debug\n"
    )
    rendered = render_import_yaml(tr)
    assert "hunter2" not in rendered
    assert find(tr, "web").env["LOG_LEVEL"] == "debug"
    assert "SECRET_KEY" in find(tr, "web").secrets


def test_bind_mount_is_flagged():
    tr = translate("services:\n  web:\n    build: .\n    volumes:\n      - ./data:/app/data\n")
    assert any("Bind mount" in t for t in note_titles(tr))


def test_database_starts_before_runtime():
    tr = translate(
        "services:\n  web:\n    build: .\n    depends_on: [db]\n"
        "  db:\n    image: postgres:16\n"
    )
    assert find(tr, "db").priority > find(tr, "web").priority


def test_only_one_service_is_public():
    tr = translate(
        'services:\n  a:\n    build: .\n    ports: ["8000:8000"]\n'
        '  b:\n    build: .\n    ports: ["9000:9000"]\n'
    )
    assert sum(1 for s in tr.services if s.public) == 1


def test_out_of_range_port_is_rejected():
    tr = translate('services:\n  web:\n    build: .\n    ports: ["80:9"]\n')
    assert any("outside the allowed range" in t for t in note_titles(tr))


def test_generated_files_pass_schema_validation():
    compose = (
        "services:\n"
        '  web:\n    build: .\n    ports: ["8000:8000"]\n    depends_on: [db, cache]\n'
        "    command: gunicorn app.wsgi\n"
        "  db:\n    image: postgres:16\n"
        "  cache:\n    image: redis:7\n"
        "  files:\n    image: minio/minio\n"
    )
    tr = translate(compose)
    assert validate_import(import_document(tr)) == []
    assert validate_zerops_yaml(zerops_document(tr)) == []


def test_docker_service_gets_no_build_section():
    tr = translate("services:\n  m:\n    image: mongo:7\n")
    setups = zerops_document(tr)["zerops"]
    assert all("build" not in s for s in setups)


def test_invalid_yaml_raises():
    with pytest.raises(TranslationError):
        translate("not: valid: yaml:")


def test_missing_services_section_raises():
    with pytest.raises(TranslationError):
        translate("version: '3'\n")
