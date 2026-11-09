"""Tests for what the deploy relies on: /healthz, no API docs, real client IPs, the deploy files."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import config, db, main
from fakes import ext, run

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


@pytest.fixture(autouse=True)
def fresh_app(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "_engine", None)
    main._recent_requests.clear()


def test_healthz():
    res = TestClient(main.app).get("/healthz")
    assert res.status_code == 200 and res.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_no_auto_generated_api_docs(path):
    assert TestClient(main.app).get(path).status_code == 404


def test_rate_limit_counts_the_forwarded_visitor_ip_not_nginx(monkeypatch):
    """What uvicorn --proxy-headers --forwarded-allow-ips 127.0.0.1 does in production:
    requests arrive from nginx (127.0.0.1) carrying the visitor's IP in X-Forwarded-For."""
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 1)
    run(monkeypatch, ext(), ext())
    behind_nginx = TestClient(ProxyHeadersMiddleware(main.app, trusted_hosts="127.0.0.1"),
                              client=("127.0.0.1", 50000))

    def say(visitor_ip):
        return behind_nginx.post("/api/chat", json={"message": "hi"},
                                 headers={"X-Forwarded-For": visitor_ip}).status_code

    assert say("203.0.113.7") == 200
    assert say("203.0.113.7") == 429   # same visitor: limited
    assert say("198.51.100.9") == 200  # another visitor: not limited by the first one


def test_forwarded_header_from_an_untrusted_host_is_ignored(monkeypatch):
    """Someone hitting uvicorn directly can't fake their IP to dodge the limit."""
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 1)
    run(monkeypatch, ext(), ext())
    direct = TestClient(ProxyHeadersMiddleware(main.app, trusted_hosts="127.0.0.1"),
                        client=("198.51.100.50", 50000))
    assert direct.post("/api/chat", json={"message": "hi"}, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert direct.post("/api/chat", json={"message": "hi"}, headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


def test_deploy_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(DEPLOY / "deploy.sh")], check=True)


def test_deploy_files_agree_on_user_port_and_domain():
    unit = (DEPLOY / "quoterunner.service").read_text()
    site = (DEPLOY / "nginx-quoterunner.conf").read_text()
    assert "User=ram" in unit and "EnvironmentFile=/opt/quoterunner/.env" in unit
    assert "--proxy-headers --forwarded-allow-ips 127.0.0.1" in unit
    assert "--port 8000" in unit and "proxy_pass http://127.0.0.1:8000;" in site
    assert "server_name quoterunner.ramiyer.tech;" in site
    assert "X-Forwarded-For $proxy_add_x_forwarded_for" in site


def test_database_defaults_to_the_data_folder_next_to_the_app():
    """On the server the app lives in /opt/quoterunner, so the database is /opt/quoterunner/data.
    Checked in a fresh process, since the fixtures above replace DB_PATH."""
    env = {k: v for k, v in os.environ.items() if k != "DB_PATH"}
    out = subprocess.run([sys.executable, "-c", "from app import config; print(config.DB_PATH)"],
                         cwd=ROOT, env=env, capture_output=True, text=True, check=True).stdout.strip()
    assert out == str(ROOT / "data" / "quoterunner.db")


# --- Automatic deploys ---------------------------------------------------------
DEPLOY_WORKFLOW = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()


def test_deploys_only_after_ci_passes_on_main():
    assert "workflows: [CI]" in DEPLOY_WORKFLOW and "branches: [main]" in DEPLOY_WORKFLOW
    assert "github.event.workflow_run.conclusion == 'success'" in DEPLOY_WORKFLOW
    assert "github.event.workflow_run.event == 'push'" in DEPLOY_WORKFLOW   # not on pull requests


def test_deploy_checks_the_host_key_and_the_deployed_commit():
    assert "known_hosts" in DEPLOY_WORKFLOW and "StrictHostKeyChecking=no" not in DEPLOY_WORKFLOW
    assert "ram@quoterunner.ramiyer.tech" in DEPLOY_WORKFLOW
    assert 'grep -q "Deployed' in DEPLOY_WORKFLOW
    assert "https://quoterunner.ramiyer.tech/healthz" in DEPLOY_WORKFLOW


def test_one_deploy_at_a_time():
    assert "group: deploy" in DEPLOY_WORKFLOW and "cancel-in-progress: false" in DEPLOY_WORKFLOW


def test_setup_locks_the_key_to_the_deploy_command():
    guide = (DEPLOY / "README.md").read_text()
    assert 'command=\\"cd /opt/quoterunner && ./deploy/deploy.sh\\",no-port-forwarding' in guide
    assert "NOPASSWD: /usr/bin/systemctl restart quoterunner" in guide
    assert "visudo -cf" in guide


def test_deploy_script_prints_the_commit_the_workflow_checks_for():
    assert 'echo "==> Deployed $(git rev-parse --short HEAD)"' in (DEPLOY / "deploy.sh").read_text()
