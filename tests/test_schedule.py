from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from sentinel.schedule import next_daily_start_after, schedule_summary


TORONTO = ZoneInfo("America/Toronto")


def timestamp(year, month, day, hour, minute, *, fold=0):
    return datetime(year, month, day, hour, minute, tzinfo=TORONTO, fold=fold).timestamp()


class ScheduleTests(unittest.TestCase):
    def test_expired_before_daily_time_waits_until_selected_time(self):
        boundary = timestamp(2026, 8, 31, 3, 32)
        self.assertEqual(
            timestamp(2026, 8, 31, 4, 0),
            next_daily_start_after(boundary, 4, 0, timezone=TORONTO),
        )

    def test_active_at_daily_time_waits_for_next_day(self):
        boundary = timestamp(2026, 8, 31, 5, 0)
        self.assertEqual(
            timestamp(2026, 9, 1, 4, 0),
            next_daily_start_after(boundary, 4, 0, timezone=TORONTO),
        )

    def test_reset_exactly_at_daily_time_uses_same_day_after_safety_buffer(self):
        boundary = timestamp(2026, 8, 31, 4, 0)
        self.assertEqual(
            boundary + 15,
            next_daily_start_after(boundary, 4, 0, timezone=TORONTO),
        )

    def test_sleep_resume_after_due_time_remains_due_without_moving_target(self):
        boundary = timestamp(2026, 8, 31, 3, 32)
        due = next_daily_start_after(boundary, 4, 0, timezone=TORONTO)
        summary = schedule_summary(
            "daily",
            boundary_reset_at=boundary,
            now=timestamp(2026, 8, 31, 7, 0),
            hour=4,
            minute=0,
            timezone=TORONTO,
        )
        self.assertEqual(timestamp(2026, 8, 31, 4, 0), due)
        self.assertTrue(summary.due)
        self.assertEqual(due, summary.next_action_at)

    def test_dst_spring_gap_normalizes_to_first_real_local_time(self):
        boundary = timestamp(2027, 3, 14, 0, 30)
        due = next_daily_start_after(boundary, 2, 30, timezone=TORONTO)
        local = datetime.fromtimestamp(due, TORONTO)
        self.assertEqual((3, 30), (local.hour, local.minute))

    def test_dst_fall_overlap_uses_one_stable_occurrence(self):
        boundary = timestamp(2026, 11, 1, 0, 30)
        first = next_daily_start_after(boundary, 1, 30, timezone=TORONTO)
        second = next_daily_start_after(boundary, 1, 30, timezone=TORONTO)
        self.assertEqual(timestamp(2026, 11, 1, 1, 30, fold=0), first)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
