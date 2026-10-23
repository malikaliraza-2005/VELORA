"""P12-T2/T3: the deployment's container rules, checked on the configuration compose runs.

`docker compose config` resolves compose.yaml (and the OpenWA overlay) exactly as a server
would. Needs Docker's compose plugin; without it these tests are skipped, unless
VELORA_REQUIRE_COMPOSE_TESTS=1 (as in CI), in which case they fail.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from velora.deploy.envfiles import StackChoices, initial_files, parse, write_initial

INFRA = Path(__file__).resolve().parents[3] / "infra"
CADDY_EDGE_IP = "172.30.10.10"
APP_SERVICES = {"api", "worker", "monitor", "backup", "ops"}
DB_CAPS = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}
# Each container's settings file: only the secrets it needs.
ENV_FILES = {
    "api": ["api.env"],
    "worker": ["worker.env"],
    "monitor": ["monitor.env"],
    "backup": ["backup.env"],
    "ops": ["ops.env", "backup.env"],
    "db": ["db.env"],
}
# Non-secret settings written in compose.yaml itself.
INLINE_SETTINGS = {
    "caddy": {"VELORA_DOMAIN", "VELORA_TLS"},
    "api": {"VELORA_HTTP_HOST", "VELORA_HTTP_PORT", "VELORA_FORWARDED_ALLOW_IPS"},
}


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(  # noqa: S603 - fixed argv
        ["docker", "compose", "version"],  # noqa: S607
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


@pytest.fixture(scope="module")
def config_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not _compose_available():
        if os.environ.get("VELORA_REQUIRE_COMPOSE_TESTS") == "1":
            pytest.fail("VELORA_REQUIRE_COMPOSE_TESTS=1 but `docker compose` is not available")
        pytest.skip("docker compose not available")
    directory = tmp_path_factory.mktemp("etc-velora")
    write_initial(directory, initial_files(StackChoices(env="production", domain="v.example")))
    return directory


def _config(config_dir: Path, *, openwa: bool = False) -> dict[str, Any]:
    files = ["-f", str(INFRA / "compose.yaml")]
    if openwa:
        files += ["-f", str(INFRA / "compose.openwa.yaml")]
    env = dict(os.environ)
    env.update(
        {
            "VELORA_CONFIG_DIR": str(config_dir),
            "VELORA_IMAGE_TAG": "0123456789ab",
            "VELORA_OPENWA_IMAGE": "example.invalid/openwa:1.2.3",
        }
    )
    result = subprocess.run(  # noqa: S603 - fixed argv
        [  # noqa: S607
            "docker",
            "compose",
            "--project-directory",
            str(INFRA),
            *files,
            "--env-file",
            str(config_dir / "stack.env"),
            "--profile",
            "jobs",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


@pytest.fixture(scope="module")
def stack(config_dir: Path) -> dict[str, Any]:
    return _config(config_dir)


@pytest.fixture(scope="module")
def stack_with_openwa(config_dir: Path) -> dict[str, Any]:
    return _config(config_dir, openwa=True)


def _services(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = config["services"]
    return services


def test_only_caddy_publishes_a_port_and_only_443(stack_with_openwa: dict[str, Any]) -> None:
    published = {
        name: [(p["published"], p["target"], p.get("protocol")) for p in svc.get("ports", [])]
        for name, svc in _services(stack_with_openwa).items()
        if svc.get("ports")
    }
    assert published == {"caddy": [("443", 443, "tcp")]}


def test_networks_separate_the_internet_from_the_database(stack: dict[str, Any]) -> None:
    networks = {name: set(svc["networks"]) for name, svc in _services(stack).items()}
    assert networks == {
        "caddy": {"public", "edge"},
        "api": {"edge", "data"},
        "worker": {"data", "egress"},
        "monitor": {"data", "egress"},
        "db": {"data"},
        "backup": {"data", "egress"},
        "ops": {"data", "egress"},
    }
    internal = {name for name, net in stack["networks"].items() if net.get("internal")}
    assert internal == {"edge", "data"}


def test_openwa_is_reachable_only_from_the_api_and_worker(
    stack_with_openwa: dict[str, Any],
) -> None:
    services = _services(stack_with_openwa)
    assert set(services["openwa"]["networks"]) == {"openwa", "openwa_egress"}
    assert stack_with_openwa["networks"]["openwa"].get("internal") is True
    on_openwa = {name for name, svc in services.items() if "openwa" in svc["networks"]}
    assert on_openwa == {"openwa", "api", "worker"}
    assert not services["openwa"].get("ports")
    # Its session volume is never backed up (P11-T2).
    for name in ("backup", "ops"):
        mounted = {v.get("source") for v in services[name].get("volumes", [])}
        assert "openwa_sessions" not in mounted


def test_proxy_headers_are_trusted_from_caddy_alone(stack: dict[str, Any]) -> None:
    services = _services(stack)
    assert services["caddy"]["networks"]["edge"]["ipv4_address"] == CADDY_EDGE_IP
    assert services["api"]["environment"]["VELORA_FORWARDED_ALLOW_IPS"] == CADDY_EDGE_IP


def test_containers_are_locked_down(stack_with_openwa: dict[str, Any]) -> None:
    for name, svc in _services(stack_with_openwa).items():
        assert "no-new-privileges:true" in svc.get("security_opt", []), name
        assert svc.get("logging", {}).get("options", {}).get("max-size"), name
        if name == "openwa":
            continue  # hardened in P5-T2, once its image is chosen (PC-4)
        assert svc.get("read_only") is True, name
        assert svc.get("cap_drop") == ["ALL"], name
        allowed = {"caddy": {"NET_BIND_SERVICE"}, "db": DB_CAPS}.get(name, set())
        assert set(svc.get("cap_add", [])) == allowed, name


def test_app_containers_run_the_pinned_release_image(stack: dict[str, Any]) -> None:
    for name, svc in _services(stack).items():
        if name in APP_SERVICES:
            assert svc["image"] == "velora-app:0123456789ab", name
            assert svc.get("init") is True, name
        else:
            assert "@sha256:" in svc["image"], f"{name} must be pinned by digest"


def test_restart_policies(stack: dict[str, Any]) -> None:
    for name, svc in _services(stack).items():
        expected = "no" if name in {"backup", "ops"} else "unless-stopped"
        assert svc.get("restart") == expected, name


def test_secrets_come_only_from_each_containers_own_file(
    stack: dict[str, Any], config_dir: Path
) -> None:
    # `compose config` folds each env file into `environment`: what remains must be exactly
    # the container's own files, plus the few non-secret settings written in compose.yaml.
    for name, svc in _services(stack).items():
        from_files: dict[str, str] = {}
        for file in ENV_FILES.get(name, []):
            from_files.update(parse((config_dir / file).read_text(encoding="utf-8")))
        environment = {k: str(v) for k, v in (svc.get("environment") or {}).items()}
        inline = {k: v for k, v in environment.items() if k not in from_files}
        assert set(inline) == INLINE_SETTINGS.get(name, set()), name
        for key, value in inline.items():
            assert "://" not in value and "PASSWORD" not in key, f"{name}: {key}"
        assert set(from_files) <= set(environment), name


def test_the_edge_sends_hsts_and_exposes_no_admin_endpoint() -> None:
    caddyfile = (INFRA / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    assert 'Strict-Transport-Security "max-age=31536000"' in caddyfile
    assert "admin off" in caddyfile
    assert "disable_http_challenge" in caddyfile  # port 80 stays closed
    assert "reverse_proxy api:8000" in caddyfile
    assert "trusted_proxies" not in caddyfile  # clients can't choose their own address


def test_the_host_script_switches_off_passwords_and_closes_everything_but_443() -> None:
    script = (INFRA / "host" / "install.sh").read_text(encoding="utf-8")
    for line in (
        "PasswordAuthentication no",
        "PermitRootLogin no",
        "ufw default deny incoming",
        "ufw allow 443/tcp",
        'ufw allow from "$source" to any port 22 proto tcp',
        'Unattended-Upgrade::Automatic-Reboot "true";',
    ):
        assert line in script, line
