"""Exact-owner lifecycle checks without starting Docker or application code."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orchestrator import build_sandbox_lifecycle as lifecycle


IMAGE = "sha256:" + "a" * 64
OWNER = "b" * 64
NAME = "workbench-build-fixture"


def reply(code=0, stdout=b"", stderr=b""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def row(nonce, *, running=False, status="created", exit_code=0):
    return {"Id": OWNER, "Name": "/" + NAME, "Image": IMAGE,
            "Config": {"Image": IMAGE, "Labels": {lifecycle.OWNER_LABEL: nonce}},
            "State": {"Status": status, "Running": running, "Pid": 0, "ExitCode": exit_code}}


class OwnedSandboxLifecycle(unittest.TestCase):
    def invoke(self, command, *, cancel=None, popen=None):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "attempt"
            with patch.object(lifecycle, "_run", side_effect=command), patch.object(lifecycle.subprocess, "Popen", return_value=popen):
                result = lifecycle.run_owned_container(["docker", "run", "--rm", "--name", NAME, IMAGE,
                    "/bin/sh", "-lc", "exit 0"], 10, NAME, cancel=cancel, image_id=IMAGE, directory=folder)
            self.assertEqual(json.loads((folder / "result.json").read_text())["cleanup_confirmed"], result["cleanup_confirmed"])
            return result

    def test_normal_execution_uses_id_and_requires_terminal_and_absence(self):
        nonce, seen = None, []
        def command(args, timeout):
            nonlocal nonce
            seen.append(args)
            if args[1] == "create":
                nonce = args[args.index("--label") + 1].split("=", 1)[1]
                self.assertNotIn("--rm", args)
                return reply(stdout=OWNER.encode() + b"\n")
            if args[1] == "rm":
                self.assertEqual(args[-1], OWNER)
                return reply()
            if args[-1] == OWNER and any(a[1] == "rm" for a in seen):
                return reply(1, stderr=("Error: No such object: " + OWNER).encode())
            status = "created" if sum(a[1] == "inspect" for a in seen) == 1 else "exited"
            return reply(stdout=json.dumps([row(nonce, status=status)]).encode())
        process = Mock(stdout=io.BytesIO(b"hello"), stderr=io.BytesIO(b""), returncode=0)
        process.poll.return_value = 0
        result = self.invoke(command, popen=process)
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["cleanup_confirmed"])
        self.assertEqual(result["stdout"], "hello")
        self.assertTrue(result["lifecycle"]["execution_completion_known"])

    def test_timed_out_create_absence_is_unknown_not_success(self):
        def command(args, timeout):
            if args[1] == "create":
                raise subprocess.TimeoutExpired(args, timeout)
            return reply(1, stdout=b"[]\n", stderr=("error: no such object: " + NAME + "\n").encode())
        result = self.invoke(command)
        self.assertIsNone(result["exit_code"])
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["cleanup_confirmed"])
        self.assertEqual(result["lifecycle"]["cleanup"]["outcome"], "create-outcome-unknown")

    def test_post_remove_absence_requires_a_nonzero_exact_id_diagnostic(self):
        observed = reply(stdout=json.dumps([row("nonce", status="exited", exit_code=1)]).encode())
        for code, subject, confirmed in ((1, OWNER, True), (1, OWNER.upper(), False),
                                         (1, "c" * 64, False), (0, OWNER, False)):
            with self.subTest(code=code, subject=subject):
                remaining = reply(code, stdout=b"[]\n", stderr=f"error: no such object: {subject}\n".encode())
                with patch.object(lifecycle, "_run", side_effect=[observed, reply(), remaining]) as command:
                    result = lifecycle.remove_owned(NAME, "nonce", IMAGE, owner=OWNER)
                self.assertEqual(result["absence_confirmed"], confirmed)
                self.assertEqual(result["confirmed"], confirmed)
                self.assertEqual(command.call_args.args[0], ["docker", "inspect", OWNER])

    def test_timed_out_create_adopts_and_removes_only_exact_owned_container(self):
        nonce, removed = None, []
        def command(args, timeout):
            nonlocal nonce
            if args[1] == "create":
                nonce = args[args.index("--label") + 1].split("=", 1)[1]
                raise subprocess.TimeoutExpired(args, timeout)
            if args[1] == "rm":
                removed.append(args[-1]); return reply()
            if args[-1] == OWNER:
                return reply(1, stderr=("Error: No such object: " + OWNER).encode())
            return reply(stdout=json.dumps([row(nonce)]).encode())
        result = self.invoke(command)
        self.assertEqual(removed, [OWNER])
        self.assertTrue(result["cleanup_confirmed"])
        self.assertIsNone(result["exit_code"])

    def test_foreign_owner_and_daemon_failure_never_trigger_remove(self):
        for inspected in (reply(stdout=json.dumps([row("foreign")]).encode()), reply(1, stderr=b"daemon unavailable")):
            with self.subTest(inspected=inspected):
                calls = []
                def command(args, timeout):
                    calls.append(args)
                    return reply(1, stderr=b"create failure") if args[1] == "create" else inspected
                result = self.invoke(command)
                self.assertFalse(result["cleanup_confirmed"])
                self.assertFalse(any(args[1] == "rm" for args in calls))

    def test_cancellation_before_dispatch_is_not_a_completed_test(self):
        event = threading.Event(); event.set()
        command = Mock(side_effect=AssertionError("No Docker dispatch allowed"))
        result = self.invoke(command, cancel=event)
        command.assert_not_called()
        self.assertTrue(result["cleanup_confirmed"])
        self.assertTrue(result["cancelled"])
        self.assertIsNone(result["exit_code"])
        self.assertFalse(result["lifecycle"]["start_dispatched"])

    def test_owner_receipt_write_failure_still_removes_created_owner(self):
        nonce, removed = None, []
        def command(args, timeout):
            nonlocal nonce
            if args[1] == "create":
                nonce = args[args.index("--label") + 1].split("=", 1)[1]
                return reply(stdout=OWNER.encode())
            if args[1] == "rm":
                removed.append(args[-1]); return reply()
            if args[-1] == OWNER and removed:
                return reply(1, stderr=("Error: No such object: " + OWNER).encode())
            return reply(stdout=json.dumps([row(nonce)]).encode())
        original = lifecycle._save
        def save(folder, name, value):
            if name == "owner.json":
                raise OSError("disk write failed")
            return original(folder, name, value)
        with patch.object(lifecycle, "_save", side_effect=save):
            result = self.invoke(command)
        self.assertEqual(removed, [OWNER])
        self.assertTrue(result["cleanup_confirmed"])
        self.assertIsNone(result["exit_code"])

    def test_known_owner_is_inspected_by_id_and_renaming_cannot_prove_cleanup(self):
        renamed = row("nonce", status="running", running=True)
        renamed["Name"] = "/renamed-owned-container"
        with patch.object(lifecycle, "_run", return_value=reply(stdout=json.dumps([renamed]).encode())) as command:
            with self.assertRaisesRegex(ValueError, "differs from owned"):
                lifecycle.remove_owned(NAME, "nonce", IMAGE, owner=OWNER)
        self.assertEqual(command.call_args.args[0], ["docker", "inspect", OWNER])
        self.assertEqual(command.call_count, 1)

    def test_actual_argv_name_and_image_must_match_before_dispatch(self):
        valid = ["docker", "run", "--rm", "--name", NAME, IMAGE, "/bin/sh", "-lc", "--rm"]
        for changed in ([*valid[:4], "foreign", *valid[5:]],
                        [*valid[:5], "sha256:" + "c" * 64, *valid[6:]],
                        [*valid[:5], "--name", NAME, *valid[5:]]):
            with self.subTest(args=changed), tempfile.TemporaryDirectory() as root:
                with patch.object(lifecycle, "_run") as command, self.assertRaises(ValueError):
                    lifecycle.run_owned_container(changed, 10, NAME, image_id=IMAGE, directory=Path(root) / "attempt")
                command.assert_not_called()
                self.assertFalse((Path(root) / "attempt").exists())
        self.assertEqual(lifecycle._validate_run_argv(valid, NAME, IMAGE), 5)


if __name__ == "__main__":
    unittest.main()
