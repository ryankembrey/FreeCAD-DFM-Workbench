# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""What the feature rules read: the part's features and its nominal wall.

``FEATURE_ANALYZER`` recognises the part once and returns one
``PartFeatures``, which every feature rule's check reads. It is plain data:
it pickles, so it can be kept with a run and checked again for another
material without measuring the part again.

``FEATURE_CACHE`` is the one recognition cache of the workbench: the
analyzer, the setup panel's "measure now" button and the recognition bench
share it, so a part recognised once isn't recognised again for the
analysis that follows (the key is the shape's geometric fingerprint, the
options, the engine version and the history hints).

Pure OCP: no FreeCAD.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from OCP.TopoDS import TopoDS_Shape

from ..recognition import ENGINE_VERSION, FeatureCache, RecognitionOptions, recognize
from ..recognition.cache import fingerprint
from ..recognition.model import DirectionalReport, Feature, FeatureKind, RecognitionResult
from . import nominal_wall

FEATURE_CACHE = FeatureCache(maxsize=8)


@dataclass(frozen=True)
class PartFeatures:
    """The recognised features of one part, for the feature rules."""

    result: RecognitionResult
    #: Draft and undercut annotations for the run's pull and side actions.
    directional: Optional[DirectionalReport]
    #: The nominal wall in mm, or None when it couldn't be measured.
    nominal_wall: Optional[float]
    #: "measured", "manual" or "none" (see core.utils.nominal_wall).
    nominal_source: str
    #: Rules skip features recognised less confidently than this.
    min_confidence: float
    #: The run's side actions (unit vectors), besides the pull direction.
    side_actions: tuple[tuple[float, float, float], ...] = ()

    @property
    def nominal_text(self) -> str:
        """The nominal wall for messages, with its source: "2.48 mm (measured)"."""
        return nominal_wall.describe(self.nominal_wall, self.nominal_source)

    def features(self, *kinds: FeatureKind) -> tuple[Feature, ...]:
        """Features of *kinds* recognised at least ``min_confidence`` confidently."""
        return tuple(f for f in self.result.of_kind(*kinds) if f.confidence >= self.min_confidence)

    def feature(self, feature_id: Optional[int]) -> Optional[Feature]:
        return self.result.by_id(feature_id) if feature_id is not None else None

    def annotation(self, feature_id: int) -> dict:
        """The directional annotations of a feature ({} without a pull direction)."""
        if self.directional is None:
            return {}
        return self.directional.feature_params(feature_id)


def recognize_cached(
    shape: TopoDS_Shape,
    options: Optional[RecognitionOptions] = None,
    hints: Iterable = (),
    progress: Optional[Callable[[str, float], None]] = None,
    check_abort: Optional[Callable[[], bool]] = None,
) -> RecognitionResult:
    """The features of *shape*, from FEATURE_CACHE or recognised now.

    History hints are only passed on when the options use them, so callers
    with and without hints share a cache entry when hints are switched off.
    """
    options = options or RecognitionOptions()
    hints = tuple(hints) if options.use_history_hints else ()
    return FEATURE_CACHE.get(shape, options, hints=hints, progress=progress, check_abort=check_abort)


def empty_result(shape: TopoDS_Shape, face_count: int, edge_count: int) -> RecognitionResult:
    """A result with no features, for a recognition that was stopped."""
    return RecognitionResult(
        features=(), face_count=face_count, edge_count=edge_count,
        fingerprint=fingerprint(shape), engine_version=ENGINE_VERSION, options={},
    )
