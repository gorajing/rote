import subprocess
import unittest

from app.digitalocean_demo import (
    DigitalOceanDemoConfig,
    DigitalOceanDemoError,
    config_from_env,
    deployment_command,
    run_demo,
)


class DigitalOceanDemoTests(unittest.TestCase):
    def test_dry_run_plans_without_mutating_cloud(self):
        result = run_demo(
            DigitalOceanDemoConfig(
                action="deploy", app_id="app-123", app_url="https://demo.example",
                live=False,
            )
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        commands = [event.get("command", "") for event in result["events"]]
        self.assertTrue(any("apps create-deployment app-123 --update-sources --wait" in cmd
                            for cmd in commands))

    def test_live_requires_app_id(self):
        with self.assertRaises(DigitalOceanDemoError):
            run_demo(DigitalOceanDemoConfig(action="restart", app_id=None, app_url=None, live=True))

    def test_rejects_unknown_action(self):
        with self.assertRaises(DigitalOceanDemoError):
            config_from_env(action="destroy", env={})

    def test_token_stays_out_of_command_argv(self):
        config = DigitalOceanDemoConfig(
            action="restart", app_id="app-123", app_url=None, live=True, token="secret-token",
        )
        command = deployment_command(config)
        self.assertNotIn("secret-token", command)

    def test_live_restart_records_status_health_and_opened_urls(self):
        calls = []
        opened = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        def fake_opener(command, **kwargs):
            opened.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        config = DigitalOceanDemoConfig(
            action="restart", app_id="app-123", app_url="https://demo.example",
            live=True, token="secret-token",
        )
        result = run_demo(
            config,
            runner=fake_runner,
            opener=fake_opener,
            health_checker=lambda url: (True, "HTTP 200"),
            doctl_path="/usr/local/bin/doctl",
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["dry_run"])
        self.assertEqual(calls[0][0][0], "/usr/local/bin/doctl")
        self.assertIn("DIGITALOCEAN_ACCESS_TOKEN", calls[0][1]["env"])
        self.assertNotIn("secret-token", " ".join(calls[0][0]))
        self.assertTrue(any(event["kind"] == "health" and event["ok"] for event in result["events"]))
        self.assertTrue(any("cloud.digitalocean.com/apps/app-123" in cmd[-1] for cmd in opened))


if __name__ == "__main__":
    unittest.main()
