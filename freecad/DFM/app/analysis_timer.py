# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import time


class AnalysisTiming:
    """Collects and reports per-analyzer, per-check, and total timing."""

    def __init__(self) -> None:
        self._total_start: float = 0.0
        self._total_elapsed: float = 0.0
        self._analyzer_times: dict[str, float] = {}
        self._check_times: dict[str, float] = {}
        self._pending: dict[str, float] = {}

    def start_total(self) -> None:
        self._total_start = time.perf_counter()

    def stop_total(self) -> None:
        self._total_elapsed = time.perf_counter() - self._total_start

    def start(self, key: str) -> None:
        self._pending[key] = time.perf_counter()

    def stop(self, key: str, bucket: dict[str, float]) -> float:
        elapsed = time.perf_counter() - self._pending.pop(key, time.perf_counter())
        bucket[key] = elapsed
        return elapsed

    def stop_analyzer(self, analyzer_id: str) -> float:
        return self.stop(analyzer_id, self._analyzer_times)

    def stop_check(self, rule_id: str) -> float:
        return self.stop(rule_id, self._check_times)

    def report(self) -> None:
        sep = "─" * 60

        print(f"\n{'═' * 60}\n  DFM Analysis Timing Report\n{'═' * 60}\n")

        if self._analyzer_times:
            print(f"  Analyzers\n  {sep}\n")
            for name, t in self._analyzer_times.items():
                print(f"  {name:<40} {self._fmt(t):>10}\n")

        if self._check_times:
            print(f"\n  Checks\n  {sep}\n")
            for name, t in self._check_times.items():
                print(f"  {name:<40} {self._fmt(t):>10}\n")

        analyzer_total = sum(self._analyzer_times.values())
        check_total = sum(self._check_times.values())

        print(f"\n  {sep}\n")
        print(f"  {'Analyzers subtotal':<40} {self._fmt(analyzer_total):>10}\n")
        print(f"  {'Checks subtotal':<40} {self._fmt(check_total):>10}\n")
        print(f"  {'Total':<40} {self._fmt(self._total_elapsed):>10}\n")
        print(f"{'═' * 60}\n\n")

    @staticmethod
    def _fmt(seconds: float) -> str:
        if seconds >= 60:
            m, s = divmod(seconds, 60)
            return f"{int(m)}m {s:.2f}s"
        if seconds >= 1:
            return f"{seconds:.3f}s"
        return f"{seconds * 1000:.1f}ms"
