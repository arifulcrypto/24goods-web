"""Synthetic backup orchestration tests; no real Docker, remote host, data or keys.

The fake age command tests failure propagation/publication only, not cryptography.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.deploy = self.root / "deploy"
        self.deploy.mkdir()
        for name in [".env", "docker-compose.yml", "docker-compose.prod.yml"]:
            (self.deploy / name).write_text("synthetic\n")
        (self.deploy / "env").mkdir()
        (self.deploy / "secrets").mkdir()
        (self.deploy / "secrets/vault_private_keys.json").write_text("synthetic-only\n")
        for name in ["recipients", "vault-recipients"]:
            (self.root / name).write_text("public-synthetic-recipient\n")
        self._exe("age", '#!/bin/sh\nprintf "synthetic-age-test\\n"\ncat\n')
        self._exe(
            "docker",
            """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_LOG"
case "$*" in
  *"ps --status running --services"*) printf 'api\ndelivery\nbepusdt\n';;
  *"pg_dump"*) if [ "$FAIL_DUMP" = 1 ]; then exit 23; fi; printf 'synthetic dump\n';;
  *"-Atc"*) printf '0011_operations_renewals\n';;
  *"images --format json"*) printf '[]\n';;
  *"run --rm"*) printf 'synthetic archive\n';;
esac
""",
        )
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + ":" + os.environ["PATH"],
            "BACKUP_DIR": str(self.root / "backups"),
            "DEPLOY_DIR": str(self.deploy),
            "BACKUP_RECIPIENTS": str(self.root / "recipients"),
            "BACKUP_VAULT_RECIPIENTS": str(self.root / "vault-recipients"),
            "FAKE_LOG": str(self.root / "docker.log"),
        }

    def _exe(self, name: str, code: str) -> None:
        p = self.bin / name
        p.write_text(code)
        p.chmod(0o700)

    def run_backup(self, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(ROOT / "deploy/backup.sh")],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_success_publishes_whole_encrypted_generation_and_resumes(self) -> None:
        r = self.run_backup()
        self.assertEqual(r.returncode, 0, r.stderr)
        complete = list((self.root / "backups").glob("*/COMPLETE"))
        self.assertEqual(len(complete), 1)
        files = {p.name for p in complete[0].parent.iterdir()}
        self.assertTrue(
            {
                "database.dump.age",
                "uploads.tgz.age",
                "bepusdt.tgz.age",
                "config.tgz.age",
                "vault-keys.tgz.age",
                "schema.txt",
                "MANIFEST.sha256",
            }
            <= files
        )
        self.assertFalse(
            any(p.suffix in (".dump", ".tgz") for p in complete[0].parent.iterdir())
        )
        self.assertIn(
            "start api delivery bepusdt", (self.root / "docker.log").read_text()
        )
        self.assertFalse((complete[0].parent / "OFFSITE_VERIFIED").exists())

    def test_dump_failure_never_publishes_and_resumes_previous_writers(self) -> None:
        r = self.run_backup(FAIL_DUMP="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(list((self.root / "backups").glob("*/COMPLETE")), [])
        self.assertIn(
            "start api delivery bepusdt", (self.root / "docker.log").read_text()
        )

    def test_missing_recovery_recipient_refuses_before_stopping_services(self) -> None:
        (self.root / "vault-recipients").unlink()
        self.assertNotEqual(self.run_backup().returncode, 0)
        self.assertFalse((self.root / "docker.log").exists())

    def test_backup_lock_refuses_concurrent_job(self) -> None:
        import fcntl

        backup = self.root / "backups"
        backup.mkdir()
        with (backup / ".backup.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.run_backup().returncode, 75)
        self.assertFalse((self.root / "docker.log").exists())

    def test_recovery_collector_never_infers_a_restore_test(self) -> None:
        self.assertEqual(self.run_backup().returncode, 0)
        spec = importlib.util.spec_from_file_location(
            "ops_collector", ROOT / "deploy/operations-status.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.collect(self.root / "backups")
        self.assertIsNotNone(out["last_complete_at"])
        self.assertFalse(out["offsite_verified"])
        self.assertIsNone(out["last_restore_test_at"])


if __name__ == "__main__":
    unittest.main()
