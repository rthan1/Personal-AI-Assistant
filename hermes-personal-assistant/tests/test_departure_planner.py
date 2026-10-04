from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from assistant.tools.departure_planner import (
    LATE,
    LEAVE_NOW,
    MAX_ESTIMATES,
    ON_TIME,
    leave_time,
    plan_departure,
)

NY = ZoneInfo("America/New_York")


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def minutes(n):
    return timedelta(minutes=n)


class Estimator:
    """Records every departure it's asked about; travel time comes from `travel_for(departure)`."""

    def __init__(self, travel_for):
        self.travel_for = travel_for
        self.calls = []

    def __call__(self, depart_at):
        self.calls.append(depart_at)
        return self.travel_for(depart_at)


def constant(n):
    return Estimator(lambda depart_at: minutes(n))


START = utc(2026, 10, 3, 18, 0)
EARLY = utc(2026, 10, 3, 12, 0)


def test_leave_time_includes_buffer():
    assert leave_time(START, travel=minutes(25), buffer=minutes(10)) == utc(2026, 10, 3, 17, 25)


def test_leave_time_rounds_down_to_the_minute():
    assert leave_time(START, travel=timedelta(minutes=25, seconds=30), buffer=minutes(0)) == utc(2026, 10, 3, 17, 34)


def test_on_time_plan_arrives_buffer_early():
    plan = plan_departure(START, minutes(10), EARLY, constant(25))
    assert plan.status == ON_TIME
    assert plan.leave_at == utc(2026, 10, 3, 17, 25)
    assert plan.arrive_at == utc(2026, 10, 3, 17, 50)
    assert plan.travel_time == minutes(25)
    assert plan.late_by == timedelta(0)


def test_zero_buffer_arrives_at_start():
    plan = plan_departure(START, minutes(0), EARLY, constant(25))
    assert plan.leave_at == utc(2026, 10, 3, 17, 35)
    assert plan.arrive_at == START


def test_max_buffer():
    plan = plan_departure(START, minutes(120), EARLY, constant(25))
    assert plan.leave_at == utc(2026, 10, 3, 15, 35)


def test_guess_moves_to_match_traffic_then_stops():
    # Initial guess assumes 30 min travel (17:20); actual is 20 min, so it re-checks 17:30 and agrees.
    estimator = constant(20)
    plan = plan_departure(START, minutes(10), EARLY, estimator)
    assert estimator.calls == [utc(2026, 10, 3, 17, 20), utc(2026, 10, 3, 17, 30)]
    assert plan.leave_at == utc(2026, 10, 3, 17, 30)


def test_first_guess_close_enough_needs_one_estimate():
    estimator = constant(31)
    plan_departure(START, minutes(10), EARLY, estimator)
    assert len(estimator.calls) == 1


def test_estimates_are_capped_when_travel_time_keeps_changing():
    answers = iter([minutes(10), minutes(60), minutes(10), minutes(60)])
    estimator = Estimator(lambda d: next(answers))
    plan_departure(START, minutes(10), EARLY, estimator)
    assert len(estimator.calls) == MAX_ESTIMATES


def test_never_asks_about_a_departure_in_the_past():
    now = utc(2026, 10, 3, 17, 30)
    estimator = constant(40)
    plan_departure(START, minutes(10), now, estimator)
    assert all(call >= now for call in estimator.calls)


def test_leave_now_when_departure_passed_but_still_arrives_before_start():
    now = utc(2026, 10, 3, 17, 40)
    plan = plan_departure(START, minutes(10), now, constant(15))
    assert plan.status == LEAVE_NOW
    assert plan.leave_at == now
    assert plan.arrive_at == utc(2026, 10, 3, 17, 55)
    assert plan.late_by == timedelta(0)


def test_late_when_leaving_now_arrives_after_start():
    now = utc(2026, 10, 3, 17, 50)
    plan = plan_departure(START, minutes(10), now, constant(25))
    assert plan.status == LATE
    assert plan.arrive_at == utc(2026, 10, 3, 18, 15)
    assert plan.late_by == minutes(15)


def test_arriving_exactly_at_start_is_not_late():
    now = utc(2026, 10, 3, 17, 35)
    plan = plan_departure(START, minutes(10), now, constant(25))
    assert plan.status == LEAVE_NOW


def test_event_already_started_is_rejected():
    with pytest.raises(ValueError, match="already started"):
        plan_departure(START, minutes(10), START, constant(25))


def test_event_after_midnight_leaves_the_evening_before():
    start = datetime(2026, 10, 4, 0, 15, tzinfo=NY)
    plan = plan_departure(start, minutes(10), utc(2026, 10, 3, 16, 0), constant(30))
    assert plan.leave_at.astimezone(NY) == datetime(2026, 10, 3, 23, 35, tzinfo=NY)


def test_departure_across_fall_back_uses_real_elapsed_time():
    # Event at 1:30 AM EST on Nov 1 (after clocks fall back); leaving 60 min earlier is 1:30 AM EDT.
    start = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=NY)
    plan = plan_departure(start, minutes(0), utc(2026, 10, 31, 20, 0), constant(60))
    assert plan.leave_at == utc(2026, 11, 1, 5, 30)
    assert plan.leave_at.astimezone(NY).utcoffset() == timedelta(hours=-4)


def test_departure_across_spring_forward():
    # Event at 3:30 AM EDT on Mar 8; leaving 60 min earlier is 1:30 AM EST (2:30 doesn't exist).
    start = datetime(2026, 3, 8, 3, 30, tzinfo=NY)
    plan = plan_departure(start, minutes(0), utc(2026, 3, 7, 20, 0), constant(60))
    local = plan.leave_at.astimezone(NY)
    assert (local.hour, local.minute) == (1, 30)
