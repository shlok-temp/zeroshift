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
