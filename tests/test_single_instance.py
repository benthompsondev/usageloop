import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import Mock

from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from sentinel.single_instance import (
    ActivationChannel,
    InstanceCoordinator,
    SingleInstanceGuard,
)


@unittest.skipUnless(os.name == "nt", "Windows named mutex behavior")
class SingleInstanceGuardTests(unittest.TestCase):
    def test_second_process_guard_is_rejected_until_first_releases(self) -> None:
        name = f"Local\\UsageLoop-test-{uuid.uuid4().hex}"
        first = SingleInstanceGuard(name)
        second = SingleInstanceGuard(name)
        third = SingleInstanceGuard(name)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        self.addCleanup(third.close)

        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())

        first.close()
        self.assertTrue(third.acquire())


class InstanceCoordinatorTests(unittest.TestCase):
    def test_first_instance_starts_activation_listener(self) -> None:
        guard = Mock()
        guard.acquire.return_value = True
        channel = Mock()

        self.assertTrue(InstanceCoordinator(guard, channel).claim(background=False))

        channel.start_primary.assert_called_once_with()
        channel.activate_existing.assert_not_called()

    def test_second_normal_launch_requests_activation(self) -> None:
        guard = Mock()
        guard.acquire.return_value = False
        channel = Mock()

        self.assertFalse(InstanceCoordinator(guard, channel).claim(background=False))

        channel.activate_existing.assert_called_once_with()
        channel.start_primary.assert_not_called()

    def test_second_background_launch_does_not_pop_window(self) -> None:
        guard = Mock()
        guard.acquire.return_value = False
        channel = Mock()

        self.assertFalse(InstanceCoordinator(guard, channel).claim(background=True))

        channel.activate_existing.assert_not_called()

    def test_ipc_failure_never_allows_a_second_controller(self) -> None:
        guard = Mock()
        guard.acquire.return_value = False
        channel = Mock()
        channel.activate_existing.return_value = False

        self.assertFalse(InstanceCoordinator(guard, channel).claim(background=False))


@unittest.skipUnless(os.name == "nt", "Windows local-server behavior")
class ActivationChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_normal_second_launch_activates_primary_once(self) -> None:
        name = f"UsageLoop-activate-test-{uuid.uuid4().hex}"
        primary = ActivationChannel(name)
        self.addCleanup(primary.close)
        activations: list[str] = []
        primary.activation_requested.connect(lambda: activations.append("activate"))

        self.assertTrue(primary.start_primary())
        child = (
            "import os; "
            "os.environ['QT_QPA_PLATFORM']='offscreen'; "
            "from PySide6.QtWidgets import QApplication; "
            "from sentinel.single_instance import ActivationChannel; "
            "app=QApplication([]); "
            f"print(ActivationChannel({name!r}).activate_existing())"
        )
        worker = subprocess.Popen(
            [sys.executable, "-c", child],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 2
        while (worker.poll() is None or not activations) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        stdout, stderr = worker.communicate(timeout=1)

        self.assertEqual(0, worker.returncode, stderr)
        self.assertEqual("True", stdout.strip())
        self.assertEqual(["activate"], activations)

    def test_activation_requires_primary_acknowledgement(self) -> None:
        name = f"UsageLoop-no-ack-test-{uuid.uuid4().hex}"
        channel = ActivationChannel(name)
        server = QLocalServer()
        self.addCleanup(server.close)
        QLocalServer.removeServer(channel.name)
        self.assertTrue(server.listen(channel.name))

        child = (
            "import os; "
            "os.environ['QT_QPA_PLATFORM']='offscreen'; "
            "from PySide6.QtWidgets import QApplication; "
            "from sentinel.single_instance import ActivationChannel; "
            "app=QApplication([]); "
            f"print(ActivationChannel({name!r}).activate_existing(timeout_ms=100))"
        )
        result = subprocess.run(
            [sys.executable, "-c", child],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("False", result.stdout.strip())

    def test_primary_recovers_a_stale_endpoint_before_listening(self) -> None:
        name = f"UsageLoop-stale-test-{uuid.uuid4().hex}"
        channel = ActivationChannel(name)
        self.addCleanup(channel.close)

        # A stale named-pipe entry may survive an abnormal process death. The
        # mutex winner is the only process allowed to remove it.
        self.assertTrue(channel.start_primary())
        channel.close()
        self.assertTrue(channel.start_primary())


if __name__ == "__main__":
    unittest.main()
