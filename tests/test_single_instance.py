import os
import unittest
import uuid

from sentinel.single_instance import SingleInstanceGuard


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


if __name__ == "__main__":
    unittest.main()
