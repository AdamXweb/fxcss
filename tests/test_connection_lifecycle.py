"""Owned Firefox startup and CLI failures, without launching a browser."""
import contextlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from fxcss import cli, core


def frame(value):
    data = json.dumps(value).encode()
    return str(len(data)).encode() + b":" + data


class ConnectionLifecycleTests(unittest.TestCase):
    def test_already_exited_process_fails_without_connecting_or_sleeping(self):
        for status in (0, 1, -9):
            with self.subTest(status=status):
                process = Mock()
                process.poll.return_value = status
                with patch.object(core.socket, "create_connection") as connect, patch.object(
                        core.time, "sleep") as sleep:
                    with self.assertRaisesRegex(core.MarionetteError, f"exited with status {status}"):
                        core.Marionette().connect(process=process)
                connect.assert_not_called()
                sleep.assert_not_called()

    def test_exit_during_connection_attempt_fails_without_retry(self):
        process = Mock()
        process.poll.return_value = None

        def refused(*args, **kwargs):
            process.poll.return_value = 7
            raise ConnectionRefusedError("refused")

        with patch.object(core.socket, "create_connection", side_effect=refused) as connect, patch.object(
                core.time, "sleep") as sleep:
            with self.assertRaisesRegex(core.MarionetteError, "exited with status 7"):
                core.Marionette().connect(process=process)
        self.assertEqual(connect.call_count, 1)
        sleep.assert_not_called()

    def test_exit_while_waiting_for_handshake_or_new_session_closes_socket(self):
        for handshake_first in (False, True):
            with self.subTest(handshake_first=handshake_first):
                process = Mock()
                process.poll.return_value = None
                sock = Mock()
                responses = [frame({"marionetteProtocol": 3})] if handshake_first else []

                def recv(*args):
                    if responses:
                        return responses.pop()
                    process.poll.return_value = 9
                    raise socket.timeout()

                sock.recv.side_effect = recv
                client = core.Marionette()
                with patch.object(core.socket, "create_connection", return_value=sock):
                    with self.assertRaisesRegex(core.MarionetteError, "exited with status 9"):
                        client.connect(process=process)
                sock.close.assert_called_once()
                self.assertIsNone(client.sock)
                self.assertIsNone(client._startup_process)

    def test_owned_startup_response_has_deadline(self):
        process = Mock()
        process.poll.return_value = None
        sock = Mock()
        sock.recv.side_effect = socket.timeout()
        with patch.object(core.socket, "create_connection", return_value=sock), patch.object(
                core.time, "monotonic", side_effect=[0, 0, 0, 2]):
            with self.assertRaisesRegex(core.MarionetteError, "timed out.*startup response"):
                core.Marionette().connect(timeout=1, process=process)
        sock.close.assert_called_once()

    def test_success_restores_command_timeout_and_stops_watching_process(self):
        process = Mock()
        process.poll.return_value = None
        sock = Mock()
        # Split frame around a timeout: startup polling must retain partial data.
        handshake = frame({"marionetteProtocol": 3})
        sock.recv.side_effect = [handshake[:5], socket.timeout(), handshake[5:],
                                 frame([1, 1, None, {}]), frame([1, 2, None, {}])]
        client = core.Marionette()
        with patch.object(core.socket, "create_connection", return_value=sock):
            client.connect(process=process)
        sock.settimeout.assert_called_with(180)
        process.poll.reset_mock()
        client.command("test")
        process.poll.assert_not_called()
        sock.close.assert_not_called()

    def test_remote_connection_retries_and_retains_existing_timeouts(self):
        sock = Mock()
        sock.recv.side_effect = [frame({"marionetteProtocol": 3}), frame([1, 1, None, {}])]
        client = core.Marionette(host="remote.example", port=1234)
        with patch.object(core.socket, "create_connection", side_effect=[
                ConnectionRefusedError(), sock]) as connect, patch.object(core.time, "sleep") as sleep:
            client.connect()
        self.assertEqual(connect.call_count, 2)
        connect.assert_called_with(("remote.example", 1234), timeout=30)
        sleep.assert_called_once_with(0.5)
        self.assertTrue(all(call.args == (180,) for call in sock.settimeout.call_args_list))

    def test_remote_timeout_reports_endpoint_and_remedy(self):
        with patch.object(core.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(core.MarionetteError, "remote.example:1234.*Marionette enabled"):
                core.Marionette("remote.example", 1234).connect(timeout=1)

    def test_handshake_transport_error_closes_socket_and_is_domain_error(self):
        sock = Mock()
        sock.recv.side_effect = ConnectionResetError("reset by peer")
        with patch.object(core.socket, "create_connection", return_value=sock):
            with self.assertRaisesRegex(core.MarionetteError, "127.0.0.1:2828.*reset by peer"):
                core.Marionette().connect()
        sock.close.assert_called_once()

    def session(self, directory):
        session = core.Session.__new__(core.Session)
        session.firefox = "/fake/firefox"
        session.workdir = Path(directory) / "owned"
        session.workdir.mkdir()
        session.profile = session.workdir / "profile"
        session.port = 1234
        session.keep_profile = False
        session.proc = session.m = None
        return session

    def test_session_exit_at_launch_cleans_profile_and_reaps_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.session(directory)
            proc = Mock()
            proc.poll.return_value = 12
            with patch.object(core.subprocess, "Popen", return_value=proc) as launch, patch.object(
                    core.socket, "create_connection") as connect:
                with self.assertRaisesRegex(core.MarionetteError, "/fake/firefox.*status 12.*--firefox"):
                    session.__enter__()
            launch.assert_called_once()
            connect.assert_not_called()
            proc.wait.assert_called_once_with(timeout=45)
            proc.kill.assert_not_called()
            self.assertFalse(session.workdir.exists())

    def test_launch_oserror_is_actionable_and_cleans_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.session(directory)
            with patch.object(core.subprocess, "Popen", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(core.MarionetteError, "could not launch.*denied.*--firefox"):
                    session.__enter__()
            self.assertFalse(session.workdir.exists())

    def test_failed_connection_kills_owned_live_process_before_waiting(self):
        for platform in ("linux", "win32"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                session = self.session(directory)
                session.proc = proc = Mock(pid=456)
                proc.poll.return_value = None
                session.m = Mock(sock=None)
                with patch.object(core.sys, "platform", platform), patch.object(
                        core.subprocess, "run") as run:
                    session.__exit__()
                if platform == "win32":
                    self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "456", "/T", "/F"])
                else:
                    proc.kill.assert_called_once()
                proc.wait.assert_called_once_with(timeout=45)
                self.assertFalse(session.workdir.exists())


class ConnectionCliTests(unittest.TestCase):
    def test_normal_marionette_failures_are_reported_without_tracebacks(self):
        for error in (core.MarionetteError("Firefox exited with status 3"),
                      core.BrowserStartupError("initial document timed out")):
            with self.subTest(error=error):
                stderr = io.StringIO()
                with patch.object(cli, "cmd_doctor", side_effect=error), contextlib.redirect_stderr(stderr):
                    self.assertEqual(cli.main(["doctor"]), 2)
                self.assertIn(f"error: {error}", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())

    def test_programming_errors_still_propagate(self):
        with patch.object(cli, "cmd_doctor", side_effect=ValueError("bug")):
            with self.assertRaisesRegex(ValueError, "bug"):
                cli.main(["doctor"])


if __name__ == "__main__":
    unittest.main()
