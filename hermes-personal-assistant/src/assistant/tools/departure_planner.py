"""Works out when to leave for an event. Pure: travel times come from an injected estimator.

Goal: arrive `buffer` before the event starts. Travel time depends on when you leave (traffic,
transit schedules) but Routes only takes a departure time, so we guess a departure, ask for the
travel time, move the guess to match, and repeat until the guess stops moving.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

ON_TIME = "on_time"  # leave at leave_at and arrive with the full buffer
LEAVE_NOW = "leave_now"  # should already have left; leaving now still arrives before the start
LATE = "late"  # leaving now arrives after the start, by late_by

INITIAL_TRAVEL_GUESS = timedelta(minutes=30)
CONVERGED_WITHIN = timedelta(minutes=2)
MAX_ESTIMATES = 3

TravelEstimator = Callable[[datetime], timedelta]


@dataclass(frozen=True)
class DeparturePlan:
    leave_at: datetime
    arrive_at: datetime
    travel_time: timedelta
    buffer: timedelta
    status: str
    late_by: timedelta = timedelta(0)


def leave_time(event_start: datetime, travel: timedelta, buffer: timedelta) -> datetime:
    """Latest departure that arrives `buffer` early, rounded down to the minute (in UTC)."""
    # Arithmetic on a zoned datetime is wall-clock, which is wrong across DST changes.
    return (event_start.astimezone(timezone.utc) - buffer - travel).replace(second=0, microsecond=0)


def plan_departure(
    event_start: datetime, buffer: timedelta, now: datetime, estimate: TravelEstimator
) -> DeparturePlan:
    event_start, now = event_start.astimezone(timezone.utc), now.astimezone(timezone.utc)
    if event_start <= now:
        raise ValueError("That event has already started, so there's no departure to plan.")

    guess = max(leave_time(event_start, INITIAL_TRAVEL_GUESS, buffer), now)
    for _ in range(MAX_ESTIMATES):
        travel = estimate(guess)
        next_guess = max(leave_time(event_start, travel, buffer), now)
        if abs(next_guess - guess) <= CONVERGED_WITHIN:
            break
        guess = next_guess

    leave_at = leave_time(event_start, travel, buffer)
    if leave_at > now:
        return DeparturePlan(leave_at, leave_at + travel, travel, buffer, ON_TIME)

    arrive_at = now + travel
    if arrive_at <= event_start:
        return DeparturePlan(now, arrive_at, travel, buffer, LEAVE_NOW)
    return DeparturePlan(now, arrive_at, travel, buffer, LATE, late_by=arrive_at - event_start)
