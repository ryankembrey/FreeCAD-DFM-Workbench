# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""How long a running analysis has left: the progress panel's "about 0:40 left".

It reads the runner's progress events (AnalysisRunner, ``events``) and works
out the time left from what is left to do, not from the share done so far:

* each analyzer costs some seconds per face (or edge) on one core. It is
  expected from earlier analyses on this computer, or else from the
  analyzer's relative_cost (rough milliseconds per face), scaled by how the
  analyzers measured so far compare with theirs. Faces differ a lot (one can
  take a hundred times another, and the cheap ones can come first), so the
  faces measured so far only bring the expectation down as more of them are
  in, while they push it up at once;
* the analyzers on the worker processes share the workers: their faces left
  divided by the workers (a little less, as they don't share out perfectly),
  but no less than their last batch;
* then the analyzers measured in FreeCAD's process, one after another, and a
  moment for each rule's check.

It would rather say too much than too little: guesses are padded, and the
total gets a margin on top. The speeds measured are kept (in the preferences)
for the next analysis, so its first guess is better.
"""

import json
import math
import time
from typing import Any, Optional

_MARGIN = 1.1  # on top of the whole estimate
_UNSURE = 1.5  # on top of an analyzer whose speed is only guessed
_SHARING = 0.9  # how well the workers share the work out (6.2x on 7 seen)
_CHECK_SECONDS = 0.05  # one rule's check, until one is timed
_COST_SECONDS = 0.001  # relative_cost is roughly milliseconds per item
_ANALYZER_SECONDS = 0.1  # setting an analyzer up, on top of its faces
_HISTORY_GROUP = "Estimates"
_HISTORY_KEY = "SecondsPerItem"


def _batch_size(count: int) -> int:
    # As BaseAnalyzer.batch_size: the most one worker takes at once.
    return max(1, min(16, math.ceil(count / 96)))


# -- Speeds from earlier analyses -------------------------------------------------


def _history_group():
    import FreeCAD as App  # type: ignore

    from .prefs import DFM_PARAM_PATH

    # A group of its own, so it isn't read as a setting (see _load_prefs).
    return App.ParamGet(f"{DFM_PARAM_PATH}/{_HISTORY_GROUP}")


def load_history() -> dict[str, float]:
    """Seconds per item measured before: {"<analyzer>:par" | ":ser" | "check": s}."""
    try:
        data = json.loads(_history_group().GetString(_HISTORY_KEY, "") or "{}")
        return {str(k): float(v) for k, v in data.items() if float(v) > 0}
    except Exception:
        return {}


def save_history(learned: dict[str, float]) -> None:
    """Fold this analysis's speeds into the ones kept (half old, half new)."""
    if not learned:
        return
    try:
        kept = load_history()
        for key, value in learned.items():
            old = kept.get(key)
            kept[key] = value if old is None else 0.5 * old + 0.5 * value
        _history_group().SetString(_HISTORY_KEY, json.dumps(kept, sort_keys=True))
    except Exception:
        pass


# -- The estimate ---------------------------------------------------------------------


class RunEstimate:
    """Time left for one analysis, fed the runner's events."""

    def __init__(self, rules: list, history: Optional[dict] = None):
        self.history = dict(history or {})
        self.analyzers: dict[str, dict] = {}  # analyzer -> {"items", "cost", "parallel"}
        for entry in rules:
            self.analyzers.setdefault(entry["analyzer"], {
                "items": max(0, int(entry.get("items", 0))),
                "cost": max(1e-3, float(entry.get("cost", 1.0))),
                "parallel": bool(entry.get("parallel", False)),
            })
        self.rules_left = len(rules)
        self.workers = 0
        self.par: dict[str, dict] = {}  # analyzers on the workers (their status)
        self.ser: dict[str, dict] = {}  # analyzers in FreeCAD's process
        self.check_times: list[float] = []
        # Until the worker processes report, it isn't known which analyzers
        # they take, so there's no estimate while some could go either way.
        self.settled = not any(a["parallel"] for a in self.analyzers.values())

    # -- Events --------------------------------------------------------------

    def on_event(self, kind: str, data: dict) -> None:
        if kind == "workers":
            self.workers = max(1, int(data.get("workers", 1)))
            self.par = {aid: dict(s) for aid, s in data.get("analyzers", {}).items()}
            self.settled = True
        elif kind == "analyzer":
            self.ser[data["id"]] = dict(data)
            self.settled = True  # measuring in FreeCAD's process: the workers are done
        elif kind == "check":
            self.rules_left = max(0, self.rules_left - 1)
            self.check_times.append(float(data.get("seconds", 0.0)))

    # -- Speeds ----------------------------------------------------------------

    def _measured(self, aid: str, mode: str) -> tuple[float, int]:
        """(seconds, items) measured so far for an analyzer in this analysis."""
        if mode == "par":
            s = self.par.get(aid)
            if s is None:
                return 0.0, 0
            return float(s.get("cpu", 0.0)), int(s.get("done", 0))
        s = self.ser.get(aid)
        if s is None:
            return 0.0, 0
        return float(s.get("seconds", 0.0)), int(s.get("done", 0))

    def _scale(self) -> Optional[float]:
        """Seconds per unit of relative_cost, from what ran fastest *for its
        cost* so far (the slowest ratio, so the guesses err long)."""
        ratios = []
        for aid, info in self.analyzers.items():
            for mode in ("par", "ser"):
                seconds, done = self._measured(aid, mode)
                if done > 0 and seconds > 0:
                    ratios.append(seconds / done / info["cost"])
        if ratios:
            return max(ratios)
        known = [self.history[f"{aid}:{m}"] / info["cost"]
                 for aid, info in self.analyzers.items() for m in ("par", "ser")
                 if f"{aid}:{m}" in self.history]
        if known:
            return max(known)
        return None

    def _expected(self, aid: str, mode: str) -> float:
        """Seconds per item expected before measuring any of its faces."""
        info = self.analyzers[aid]
        other = "ser" if mode == "par" else "par"
        known = self.history.get(f"{aid}:{mode}") or self.history.get(f"{aid}:{other}")
        if known is not None:
            return known
        scale = max(self._scale() or 0.0, _COST_SECONDS)
        return info["cost"] * scale * _UNSURE

    def _rate(self, aid: str, mode: str) -> float:
        """Seconds per item for an analyzer's faces still to measure (one core)."""
        info = self.analyzers[aid]
        expected = self._expected(aid, mode)
        seconds, done = self._measured(aid, mode)
        if done <= 0 or seconds <= 0:
            return expected
        seen = seconds / done
        if seen >= expected:
            return seen
        # Faster than expected so far: believe it only as far as it has
        # measured (half its faces count as much as the expectation).
        weight = max(3.0, 0.5 * info["items"])
        return (seconds + expected * weight) / (done + weight)

    # -- Time left -----------------------------------------------------------------

    def remaining(self) -> Optional[float]:
        """Seconds left, or None until it's known what the workers take on."""
        if not self.settled:
            return None

        parallel = 0.0
        if self.par:
            work, tail = 0.0, 0.0
            for aid, s in self.par.items():
                if s.get("finished") is not None or aid not in self.analyzers:
                    continue
                rate = self._rate(aid, "par")
                total = int(s.get("total", 0))
                work += max(0, total - int(s.get("done", 0))) * rate
                tail = max(tail, _batch_size(total) * rate)
            parallel = max(work / (max(1, self.workers) * _SHARING), tail) if work else 0.0

        serial = 0.0
        for aid, info in self.analyzers.items():
            if aid in self.par:
                continue
            s = self.ser.get(aid)
            if s is not None and s.get("state") == "done":
                continue
            rate = self._rate(aid, "ser")
            total = info["items"]
            if s is None:
                serial += total * rate + _ANALYZER_SECONDS
            else:
                left = max(0, total - int(s.get("done", 0))) * rate
                if not s.get("done"):  # still setting up: count what it has spent
                    left = max(total * rate - float(s.get("seconds", 0.0)), 0.1 * total * rate)
                serial += left

        check = (max(self.check_times) if self.check_times
                 else self.history.get("check", _CHECK_SECONDS))
        checks = self.rules_left * check
        return (parallel + serial + checks) * _MARGIN

    def learned(self) -> dict[str, float]:
        """Speeds measured in this (finished) analysis, to keep for the next."""
        out: dict[str, float] = {}
        for aid, info in self.analyzers.items():
            for mode in ("par", "ser"):
                seconds, done = self._measured(aid, mode)
                if done > 0 and seconds > 0 and done >= info["items"]:
                    out[f"{aid}:{mode}"] = seconds / done
        if self.check_times:
            out["check"] = sum(self.check_times) / len(self.check_times)
        return out


class ShownEstimate:
    """The time left as shown: it goes up at once, but comes down gently
    (over a second or two), so it doesn't leap about as speeds are learned;
    and it counts down by itself between updates."""

    def __init__(self, settle: float = 1.5):
        self.settle = settle
        self.value: Optional[float] = None
        self.at = 0.0

    def update(self, estimate: Optional[float], now: Optional[float] = None) -> Optional[float]:
        now = time.monotonic() if now is None else now
        if estimate is None:
            return self.value if self.value is None else max(0.0, self.value - (now - self.at))
        if self.value is None:
            self.value, self.at = estimate, now
            return self.value
        dt = max(0.0, now - self.at)
        counted = max(0.0, self.value - dt)
        if estimate >= counted:
            self.value = estimate
        else:
            self.value = max(estimate, counted - (counted - estimate) * min(1.0, dt / self.settle))
        self.at = now
        return self.value


def describe(seconds: float) -> str:
    """"about 1:20 left", rounded up (better to finish early than late)."""
    if seconds < 5:
        return "a few seconds left"
    if seconds < 60:
        return f"about {int(math.ceil(seconds / 5.0) * 5)} s left"
    step = 10 if seconds < 600 else 30
    seconds = int(math.ceil(seconds / step) * step)
    minutes, secs = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"about {hours}:{minutes:02d}:{secs:02d} left"
    return f"about {minutes}:{secs:02d} left"
