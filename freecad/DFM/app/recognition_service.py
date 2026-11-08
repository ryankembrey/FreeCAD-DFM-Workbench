# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Feature recognition as a shape-level service for the workbench.

Recognition (core/recognition) runs once per shape, not per face, so it
doesn't fit the per-face analyzer model: as an analyzer it would rerun in
every worker process. Instead the workbench asks this service, which:

* builds RecognitionOptions from the DFM preferences (Preferences > DFM >
  Feature Recognition; every threshold is there);
* adds hints from the model's history (PartDesign features) when that
  preference is on;
* caches results per shape (geometric fingerprint + options + engine
  version), so analyzers, checks and the bench share one result.

How analyzers and checks will use it (not wired in yet; see DESIGN.md §7):
AnalysisRunner calls ``features_for(shape_occ, target_object)`` once when a
check or analyzer declares ``requires_features = True`` and passes the
result in ``kwargs["features"]``. The result is plain data (face numbers,
floats, strings), so it pickles to the worker processes cheaply.
"""

from __future__ import annotations

from typing import Optional

from ..core.recognition import FeatureCache, RecognitionOptions, RecognitionResult, options_from_prefs, pref_key
from ..core.recognition.options import RecognitionOptions as _Options
from .prefs import dfm_params

_CACHE = FeatureCache(maxsize=8)

#: The developer switch that shows the recognition test bench.
BENCH_PREF = "DeveloperRecognitionBench"
#: Where the bench keeps its corpus (empty = the repository's tests folder).
CORPUS_PREF = "RecognitionCorpusDir"


def read_options() -> RecognitionOptions:
    """RecognitionOptions from the DFM preferences (defaults where unset)."""
    params = dfm_params()
    defaults = _Options()
    values = {}
    for f in _Options.preference_fields():
        key = pref_key(f.name)
        default = getattr(defaults, f.name)
        if isinstance(default, bool):
            values[key] = params.GetBool(key, default)
        elif isinstance(default, int):
            values[key] = params.GetInt(key, default)
        else:
            values[key] = params.GetFloat(key, default)
    return options_from_prefs(values)


def write_options(options: RecognitionOptions) -> None:
    params = dfm_params()
    for f in _Options.preference_fields():
        key = pref_key(f.name)
        value = getattr(options, f.name)
        if isinstance(value, bool):
            params.SetBool(key, value)
        elif isinstance(value, int):
            params.SetInt(key, value)
        else:
            params.SetFloat(key, float(value))


def bench_enabled() -> bool:
    try:
        return dfm_params().GetBool(BENCH_PREF, False)
    except Exception:
        return False


def corpus_dir() -> str:
    from ..core.recognition.testing.corpus import default_corpus_dir

    try:
        path = dfm_params().GetString(CORPUS_PREF, "")
    except Exception:
        path = ""
    return path or default_corpus_dir()


def features_for(shape_occ, target_object=None, options: Optional[RecognitionOptions] = None,
                 use_cache: bool = True, **kwargs) -> RecognitionResult:
    """Recognise *shape_occ* (a TopoDS_Shape), with history hints from
    *target_object* when it has history and the preference allows."""
    options = options or read_options()
    hints = []
    if target_object is not None and options.use_history_hints:
        try:
            from .recognition_hints import hints_for
            hints = hints_for(target_object)
        except Exception:
            hints = []
    if use_cache:
        return _CACHE.get(shape_occ, options, hints=hints, **kwargs)
    from ..core.recognition import recognize
    return recognize(shape_occ, options, hints=hints, **kwargs)


def features_for_object(obj, options: Optional[RecognitionOptions] = None, **kwargs):
    """(shape_occ, result) for a FreeCAD object with a Shape."""
    from ..core.utils.conversion import freecad_to_ocp

    shape_occ = freecad_to_ocp(obj.Shape)
    return shape_occ, features_for(shape_occ, obj, options, **kwargs)


def clear_cache() -> None:
    _CACHE.clear()
