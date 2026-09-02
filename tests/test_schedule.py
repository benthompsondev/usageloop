from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from sentinel.schedule import (
    WEEKLY,
    next_daily_start_after,
    next_weekly_start_after,
    schedule_summary,
)


TORONTO = ZoneInfo("America/Toronto")
WEEKLY_TIMES = (
    (4, 0),
    (4, 0),
    (4, 0),
    (4, 0),
    (4, 0),
    (5, 0),
    (5, 0),
)


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
            boundary + 60,
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

    def test_continuous_is_not_due_until_the_full_safety_minute(self):
        boundary = timestamp(2026, 8, 31, 3, 32)
        at_reset = schedule_summary(
            "continuous", boundary_reset_at=boundary, now=boundary
        )
        at_59 = schedule_summary(
            "continuous", boundary_reset_at=boundary, now=boundary + 59
        )
        at_60 = schedule_summary(
            "continuous", boundary_reset_at=boundary, now=boundary + 60
        )
        self.assertFalse(at_reset.due)
        self.assertFalse(at_59.due)
        self.assertTrue(at_60.due)
        self.assertEqual(boundary + 60, at_60.next_action_at)

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

    def test_daily_schedule_crosses_year_boundary(self):
        boundary = timestamp(2026, 12, 31, 23, 30)
        self.assertEqual(
            timestamp(2027, 1, 1, 4, 0),
            next_daily_start_after(boundary, 4, 0, timezone=TORONTO),
        )

    def test_clock_moving_back_before_due_does_not_make_schedule_early(self):
        boundary = timestamp(2026, 8, 31, 3, 32)
        summary = schedule_summary(
            "daily",
            boundary_reset_at=boundary,
            now=timestamp(2026, 8, 31, 3, 0),
            hour=4,
            minute=0,
            timezone=TORONTO,
        )
        self.assertFalse(summary.due)
        self.assertEqual(timestamp(2026, 8, 31, 4, 0), summary.next_action_at)

    def test_timezone_change_reinterprets_daily_time_in_the_new_local_zone(self):
        london = ZoneInfo("Europe/London")
        boundary = timestamp(2026, 8, 31, 3, 32)

        toronto_due = next_daily_start_after(boundary, 4, 0, timezone=TORONTO)
        london_due = next_daily_start_after(boundary, 4, 0, timezone=london)

        self.assertEqual(4, datetime.fromtimestamp(toronto_due, TORONTO).hour)
        self.assertEqual(4, datetime.fromtimestamp(london_due, london).hour)
        self.assertNotEqual(toronto_due, london_due)

    def test_weekly_target_uses_saturday_time_after_friday(self):
        self.assertEqual(
            timestamp(2026, 8, 29, 5, 0),
            next_weekly_start_after(
                timestamp(2026, 8, 28, 23, 0),
                WEEKLY_TIMES,
                timezone=TORONTO,
            ),
        )

    def test_weekly_target_uses_monday_time_after_sunday(self):
        self.assertEqual(
            timestamp(2026, 8, 31, 4, 0),
            next_weekly_start_after(
                timestamp(2026, 8, 30, 6, 0),
                WEEKLY_TIMES,
                timezone=TORONTO,
            ),
        )

    def test_weekly_pause_starts_exactly_five_hours_before_next_target(self):
        boundary = timestamp(2026, 8, 30, 22, 0)
        before = schedule_summary(
            WEEKLY,
            boundary_reset_at=boundary,
            now=timestamp(2026, 8, 30, 22, 59),
            weekly_times=WEEKLY_TIMES,
            timezone=TORONTO,
        )
        at_pause = schedule_summary(
            WEEKLY,
            boundary_reset_at=boundary,
            now=timestamp(2026, 8, 30, 23, 0),
            weekly_times=WEEKLY_TIMES,
            timezone=TORONTO,
        )

        self.assertTrue(before.due)
        self.assertEqual("continuous_rollover", before.phase)
        self.assertFalse(at_pause.due)
        self.assertEqual("overnight_pause", at_pause.phase)
        self.assertEqual(timestamp(2026, 8, 31, 4, 0), at_pause.next_action_at)

    def test_weekly_daytime_reset_rolls_continuously(self):
        boundary = timestamp(2026, 8, 31, 9, 0)
        summary = schedule_summary(
            WEEKLY,
            boundary_reset_at=boundary,
            now=timestamp(2026, 8, 31, 9, 1),
            weekly_times=WEEKLY_TIMES,
            timezone=TORONTO,
        )

        self.assertTrue(summary.due)
        self.assertEqual(boundary + 60, summary.next_action_at)
        self.assertEqual("continuous_rollover", summary.phase)

    def test_weekly_active_window_crossing_first_start_waits_for_real_reset(self):
        boundary = timestamp(2026, 8, 31, 8, 0)
        summary = schedule_summary(
            WEEKLY,
            boundary_reset_at=boundary,
            now=timestamp(2026, 8, 31, 4, 0),
            weekly_times=WEEKLY_TIMES,
            timezone=TORONTO,
        )

        self.assertFalse(summary.due)
        self.assertEqual(boundary + 60, summary.next_action_at)
        self.assertEqual("active_window", summary.phase)

    def test_weekly_sleep_after_first_start_catches_up_once(self):
        boundary = timestamp(2026, 8, 31, 2, 0)
        summary = schedule_summary(
            WEEKLY,
            boundary_reset_at=boundary,
            now=timestamp(2026, 8, 31, 7, 0),
            weekly_times=WEEKLY_TIMES,
            timezone=TORONTO,
        )

        self.assertTrue(summary.due)
        self.assertEqual(timestamp(2026, 8, 31, 4, 0), summary.next_action_at)
        self.assertEqual("scheduled_first_start", summary.phase)

    def test_weekly_sleep_before_first_start_remains_in_overnight_pause(self):
        summary = schedule_summary(
            WEEKLY,
            boundary_reset_at=timestamp(2026, 8, 30, 22, 0),
            now=timestamp(2026, 8, 31, 3, 0),
            weekly_times=WEEKLY_TIMES,
            timezone=TORONTO,
        )

        self.assertFalse(summary.due)
        self.assertEqual(timestamp(2026, 8, 31, 4, 0), summary.next_action_at)
        self.assertEqual("overnight_pause", summary.phase)

    def test_weekly_reset_inside_pause_waits_for_first_start(self):
        summary = schedule_summary(
            WEEKLY,
            boundary_reset_at=timestamp(2026, 8, 31, 1, 0),
            now=timestamp(2026, 8, 30, 23, 30),
            weekly_times=WEEKLY_TIMES,
            timezone=TORONTO,
        )

        self.assertFalse(summary.due)
        self.assertEqual(timestamp(2026, 8, 31, 4, 0), summary.next_action_at)
        self.assertEqual("overnight_pause", summary.phase)

    def test_weekly_spring_gap_normalizes_selected_sunday_time(self):
        spring = WEEKLY_TIMES[:-1] + ((2, 30),)
        due = next_weekly_start_after(
            timestamp(2027, 3, 13, 23, 0), spring, timezone=TORONTO
        )

        self.assertEqual((3, 30), (
            datetime.fromtimestamp(due, TORONTO).hour,
            datetime.fromtimestamp(due, TORONTO).minute,
        ))

    def test_weekly_fall_overlap_uses_first_selected_occurrence(self):
        fall = WEEKLY_TIMES[:-1] + ((1, 30),)
        due = next_weekly_start_after(
            timestamp(2026, 10, 31, 23, 0), fall, timezone=TORONTO
        )

        self.assertEqual(timestamp(2026, 11, 1, 1, 30, fold=0), due)

    def test_weekly_supports_midnight_and_noon(self):
        times = ((12, 0),) + WEEKLY_TIMES[1:6] + ((0, 0),)

        monday = next_weekly_start_after(
            timestamp(2026, 8, 31, 0, 0), times, timezone=TORONTO
        )
        sunday = next_weekly_start_after(
            timestamp(2026, 8, 29, 23, 0), times, timezone=TORONTO
        )

        self.assertEqual(timestamp(2026, 8, 31, 12, 0), monday)
        self.assertEqual(timestamp(2026, 8, 30, 0, 0), sunday)


if __name__ == "__main__":
    unittest.main()
