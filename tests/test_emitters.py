import yaml

from app.emitters import render_zerops_yaml
from app.translate import translate


def test_python_dependencies_reach_the_runtime_container():
    """Build and runtime are separate containers; pip install must be repeated."""
    compose = """
services:
  api:
    build: .
    ports: ["8000:8000"]
    command: uvicorn app.main:app --port 8000
"""
    doc = yaml.safe_load(render_zerops_yaml(translate(compose)))
    setup = doc["zerops"][0]
    assert setup["build"]["addToRunPrepare"] == ["requirements.txt"]
    assert setup["run"]["prepareCommands"] == ["pip install -r requirements.txt"]


def test_managed_database_gets_no_prepare_commands():
    compose = "services:\n  db:\n    image: postgres:16\n"
    doc = yaml.safe_load(render_zerops_yaml(translate(compose)))
    for setup in doc["zerops"]:
        assert "prepareCommands" not in setup.get("run", {})


MULTI_SERVICE = """
services:
  frontend:
    build: ./frontend
    image: node:20
    ports: ["3000:3000"]
  api:
    build: ./api
    image: node:20
    ports: ["4000:4000"]
    command: node server.js
    depends_on: [db]
  db:
    image: postgres:16
"""


def test_yaml_has_no_anchors_or_aliases():
    """Services sharing a list object must not emit &id / *id references."""
    out = render_zerops_yaml(translate(MULTI_SERVICE))
    assert "&id" not in out
    assert "*id" not in out


def test_credentials_go_only_to_dependent_services():
    doc = yaml.safe_load(render_zerops_yaml(translate(MULTI_SERVICE)))
    setups = {s["setup"]: s for s in doc["zerops"]}
    assert "DB_PASS" in setups["api"]["run"]["envVariables"]
    assert "DB_PASS" not in setups["frontend"]["run"].get("envVariables", {})
