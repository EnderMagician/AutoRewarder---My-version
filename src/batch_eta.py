"""Time-left estimate for a multi-account batch.

The estimate starts from what a search should take (the configured delay,
typing, reading, and the average share of coffee breaks and random pauses)
and moves toward the measured pace as searches complete. The pauses
between accounts are planned up front, so they count exactly. On top of
that come fixed costs per account: starting and closing the browser for
each phase, the pause between phases and, when included, the Daily tasks.
"""

import time

# Seconds to start and close the browser for one phase.
PHASE_OVERHEAD_SECONDS = 15.0
# Rough time for the Daily Set and More Activities on one account.
DAILY_TASKS_SECONDS = 240.0
# How many measured searches the prior estimate is worth. Higher keeps the
# estimate steadier early on; lower follows the measured pace sooner.
PRIOR_WEIGHT = 6
# A gap between two searches longer than this is not a search (the machine
# slept, the browser hung); it is left out of the measured pace.
MAX_SEARCH_SAMPLE = 900.0


class BatchEta:
    """Track how much of a batch is left and estimate the time it needs."""

    def __init__(
        self,
        plans,
        seconds_per_search,
        phase_gap_mean=0.0,
        include_daily_tasks=False,
        timed_accounts=None,
        clock=time.monotonic,
    ):
        """
        Args:
            plans (list[AccountPlan]): the batch, in run order.
            seconds_per_search (float): the prior estimate for one search.
            phase_gap_mean (float): average pause between PC and Mobile.
            include_daily_tasks (bool): whether each account runs Daily tasks.
            timed_accounts (dict | None): account id -> seconds, for accounts
                whose run length is set by Advanced Scheduling instead of
                their search count.
            clock (callable): monotonic seconds, for tests.
        """
        self._clock = clock
        self._prior = max(1.0, float(seconds_per_search))
        self._measured_total = 0.0
        self._measured_count = 0
        self._last_search = None

        timed_accounts = timed_accounts or {}
        self._searches = {}
        self._fixed = {}
        self._gaps = {}
        self._timed = {}
        for plan in plans:
            account_id = plan.account["id"]
            phases = int(plan.pc_count > 0) + int(plan.mobile_count > 0)
            fixed = phases * PHASE_OVERHEAD_SECONDS
            if phases == 2:
                fixed += phase_gap_mean
            if include_daily_tasks and plan.pc_count > 0:
                fixed += DAILY_TASKS_SECONDS
            self._gaps[account_id] = float(plan.gap_before)
            if account_id in timed_accounts:
                self._timed[account_id] = float(timed_accounts[account_id])
                self._searches[account_id] = 0
                self._fixed[account_id] = 0.0
            else:
                self._searches[account_id] = plan.pc_count + plan.mobile_count
                self._fixed[account_id] = fixed

        self._phase_gap_mean = phase_gap_mean
        self._current = None
        self._current_started = None
        self._phases_started = 0
        self._wait_until = None

    # -- events --------------------------------------------------------

    def start_wait(self, account_id):
        """The pause before `account_id` has begun."""
        self._wait_until = self._clock() + self._gaps.get(account_id, 0.0)
        self._gaps[account_id] = 0.0

    def start_account(self, account_id):
        """`account_id` has started running."""
        self._wait_until = None
        self._gaps[account_id] = 0.0
        self._current = account_id
        self._current_started = self._clock()
        self._phases_started = 0
        self._last_search = None

    def start_phase(self):
        """A PC or Mobile phase of the current account has started."""
        if self._current is None:
            return
        self._phases_started += 1
        spent = PHASE_OVERHEAD_SECONDS
        if self._phases_started == 2:
            spent += self._phase_gap_mean
        self._fixed[self._current] = max(0.0, self._fixed[self._current] - spent)
        # Searches are timed against each other, never across a phase change
        # (browser start, Daily tasks and the phase pause have their own costs).
        self._last_search = None

    def search_done(self):
        """One search attempt of the current account has finished."""
        if self._current is None:
            return
        now = self._clock()
        if self._last_search is not None:
            sample = now - self._last_search
            if 0 < sample <= MAX_SEARCH_SAMPLE:
                self._measured_total += sample
                self._measured_count += 1
        self._last_search = now
        left = self._searches.get(self._current, 0)
        self._searches[self._current] = max(0, left - 1)

    def finish_account(self, account_id):
        """`account_id` is done; nothing of it is left."""
        self._searches[account_id] = 0
        self._fixed[account_id] = 0.0
        self._timed.pop(account_id, None)
        self._gaps[account_id] = 0.0
        if self._current == account_id:
            self._current = None

    # -- estimates -----------------------------------------------------

    def seconds_per_search(self):
        """The prior blended with the measured pace."""
        return (self._prior * PRIOR_WEIGHT + self._measured_total) / (
            PRIOR_WEIGHT + self._measured_count
        )

    def remaining_searches(self):
        return sum(self._searches.values())

    def seconds_left(self):
        """Estimated seconds until the whole batch is done."""
        now = self._clock()
        total = self.remaining_searches() * self.seconds_per_search()
        total += sum(self._fixed.values()) + sum(self._gaps.values())
        if self._wait_until is not None:
            total += max(0.0, self._wait_until - now)
        for account_id, seconds in self._timed.items():
            if account_id == self._current and self._current_started is not None:
                seconds -= now - self._current_started
            total += max(0.0, seconds)
        return max(0.0, total)


def format_eta(seconds):
    """Coarse wording for a time left: 'under a minute', '12 min', '1 h 05 min'."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return "under a minute"
    minutes = (seconds + 30) // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"
