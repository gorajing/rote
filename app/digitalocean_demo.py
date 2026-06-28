"""DigitalOcean partner-demo action for the voice agent.

This is intentionally a tiny, deterministic runner rather than a new inference layer:
Rote can narrate and execute a preconfigured DigitalOcean App Platform restart/deploy in
seconds, then open the dashboard/live URL. Dry-run is the default so hackathon rehearsals
do not accidentally mutate cloud state.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / ".env")

TOKEN_KEYS = ("DIGITALOCEAN_ACCESS_TOKEN", "DO_ACCESS_TOKEN", "DIGITALOCEAN_TOKEN")
APP_ID_KEYS = ("DO_APP_ID", "ROTE_DO_APP_ID", "DIGITALOCEAN_APP_ID")
APP_URL_KEYS = ("DO_APP_URL", "ROTE_DO_APP_URL", "DIGITALOCEAN_APP_URL")
CONTEXT_KEYS = ("DOCTL_CONTEXT", "ROTE_DOCTL_CONTEXT")
LIVE_KEYS = ("ROTE_DO_DEMO_LIVE", "DO_DEMO_LIVE")
ALLOWED_ACTIONS = {"restart", "deploy"}


class DigitalOceanDemoError(RuntimeError):
    """Raised when a live DigitalOcean demo cannot be run safely."""


@dataclass(frozen=True)
class DigitalOceanDemoConfig:
    action: str
    app_id: str | None
    app_url: str | None
    live: bool
    open_urls: bool = True
    timeout_s: float = 180.0
    context: str | None = None
    token: str | None = None


def _first_env(keys: tuple[str, ...], env: dict[str, str] | None = None) -> str | None:
    source = env if env is not None else os.environ
    for key in keys:
        value = source.get(key)
        if value:
            return value
    return None


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "live"}


def config_from_env(
    *,
    action: str | None = None,
    live: bool | None = None,
    app_id: str | None = None,
    app_url: str | None = None,
    open_urls: bool = True,
    env: dict[str, str] | None = None,
) -> DigitalOceanDemoConfig:
    source = env if env is not None else os.environ
    selected_action = (action or source.get("ROTE_DO_ACTION") or "restart").strip().lower()
    if selected_action not in ALLOWED_ACTIONS:
        raise DigitalOceanDemoError(
            f"unsupported DigitalOcean action: {selected_action!r}; choose restart or deploy"
        )
    return DigitalOceanDemoConfig(
        action=selected_action,
        app_id=app_id or _first_env(APP_ID_KEYS, source),
        app_url=app_url or _first_env(APP_URL_KEYS, source),
        live=_truthy(_first_env(LIVE_KEYS, source)) if live is None else bool(live),
        open_urls=open_urls,
        context=_first_env(CONTEXT_KEYS, source),
        token=_first_env(TOKEN_KEYS, source),
    )


def _doctl_prefix(config: DigitalOceanDemoConfig) -> list[str]:
    cmd = ["doctl"]
    if config.context:
        cmd += ["--context", config.context]
    return cmd


def deployment_command(config: DigitalOceanDemoConfig) -> list[str]:
    if not config.app_id:
        raise DigitalOceanDemoError("DO_APP_ID is required for a live DigitalOcean demo")
    if config.action == "deploy":
        return _doctl_prefix(config) + [
            "apps", "create-deployment", config.app_id, "--update-sources", "--wait",
            "--format", "ID,Progress,Phase,Created,Updated",
        ]
    return _doctl_prefix(config) + [
        "apps", "restart", config.app_id, "--wait",
        "--format", "ID,Cause,Progress,Phase,Created,Updated",
    ]


def status_command(config: DigitalOceanDemoConfig) -> list[str]:
    if not config.app_id:
        raise DigitalOceanDemoError("DO_APP_ID is required for a live DigitalOcean demo")
    return _doctl_prefix(config) + [
        "apps", "list-deployments", config.app_id,
        "--format", "ID,Phase,Progress,Updated", "--no-header",
    ]


def dashboard_url(app_id: str | None) -> str | None:
    return f"https://cloud.digitalocean.com/apps/{app_id}" if app_id else None


def _redacted(command: list[str]) -> str:
    return " ".join(command)


def _run_command(
    command: list[str],
    config: DigitalOceanDemoConfig,
    *,
    runner: Callable[..., subprocess.CompletedProcess],
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if config.token:
        env["DIGITALOCEAN_ACCESS_TOKEN"] = config.token
    return runner(command, check=False, capture_output=True, text=True,
                  timeout=config.timeout_s, env=env)


def _check_health(url: str, *, timeout_s: float = 8.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            return 200 <= int(response.status) < 500, f"HTTP {response.status}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def run_demo(
    config: DigitalOceanDemoConfig,
    *,
    narrator: Callable[[str], None] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    opener: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    health_checker: Callable[[str], tuple[bool, str]] = _check_health,
    doctl_path: str | None = None,
) -> dict:
    """Run or rehearse the DigitalOcean partner demo.

    Returns a JSON-serializable record. In dry-run mode no external command is executed.
    """
    say = narrator or (lambda _text: None)
    deploy_cmd = deployment_command(config) if config.app_id else None
    status_cmd = status_command(config) if config.app_id else None
    dash_url = dashboard_url(config.app_id)
    events: list[dict] = []

    if not config.live:
        say("I have the DigitalOcean runbook loaded. Dry run first: no cloud state will change.")
        if deploy_cmd:
            events.append({"kind": "plan", "command": _redacted(deploy_cmd)})
        else:
            events.append({"kind": "missing_config", "message": "set DO_APP_ID for the live app"})
        if dash_url:
            events.append({"kind": "dashboard", "url": dash_url})
        if config.app_url:
            events.append({"kind": "app", "url": config.app_url})
        return {"ok": True, "dry_run": True, "action": config.action, "events": events}

    if not config.app_id:
        raise DigitalOceanDemoError("DO_APP_ID is required; refusing to run a live demo")
    if doctl_path is None:
        doctl_path = shutil.which("doctl")
    if not doctl_path:
        raise DigitalOceanDemoError("doctl is not installed; run `brew install doctl` first")

    say("On it. I am running the learned DigitalOcean operations path now.")
    assert deploy_cmd is not None and status_cmd is not None
    deploy_cmd = [doctl_path] + deploy_cmd[1:]
    status_cmd = [doctl_path] + status_cmd[1:]

    events.append({"kind": "deploy_start", "command": _redacted(deploy_cmd)})
    say("Triggering the DigitalOcean app action and waiting for it to settle.")
    deployment = _run_command(deploy_cmd, config, runner=runner)
    events.append({
        "kind": "deploy_done",
        "returncode": deployment.returncode,
        "stdout": deployment.stdout[-1200:],
        "stderr": deployment.stderr[-1200:],
    })
    if deployment.returncode != 0:
        raise DigitalOceanDemoError((deployment.stderr or deployment.stdout or "doctl failed").strip())

    say("Now I am checking the latest deployment state.")
    status = _run_command(status_cmd, config, runner=runner)
    events.append({
        "kind": "status",
        "returncode": status.returncode,
        "stdout": status.stdout[-1200:],
        "stderr": status.stderr[-1200:],
    })

    if config.app_url:
        say("Verifying the live app endpoint.")
        ok, detail = health_checker(config.app_url)
        events.append({"kind": "health", "ok": ok, "url": config.app_url, "detail": detail})
        if not ok:
            raise DigitalOceanDemoError(f"live app health check failed: {detail}")

    if config.open_urls:
        for url in (dash_url, config.app_url):
            if url:
                opener(["open", url], check=False, capture_output=True, text=True)
                events.append({"kind": "opened", "url": url})
    say("DigitalOcean is live. The dashboard and app are open.")
    return {"ok": True, "dry_run": False, "action": config.action, "events": events}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=sorted(ALLOWED_ACTIONS), default=None)
    parser.add_argument("--live", action="store_true", help="actually run doctl")
    parser.add_argument("--no-open", action="store_true", help="do not open dashboard/app URLs")
    args = parser.parse_args()
    config = config_from_env(action=args.action, live=args.live, open_urls=not args.no_open)
    result = run_demo(config, narrator=print)
    print(result)


if __name__ == "__main__":
    main()
