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

The feature rules get their features from FEATURE_ANALYZER
(core/analyzers/feature_analyzer.py), which shares this service's cache
(core.utils.part_features.FEATURE_CACHE): a part measured from the setup
panel, or inspected in the bench, isn't recognised again by the analysis.
The analysis is given the same history hints (``history_hints``), so it
finds the same cache entry.
"""

from __future__ import annotations

from typing import Optional

from ..core.recognition import RecognitionOptions, RecognitionResult, options_from_prefs, pref_key
from ..core.recognition.options import RecognitionOptions as _Options
from ..core.utils.part_features import FEATURE_CACHE
from .prefs import dfm_params

#: One cache for the bench, the setup panel and the analysis.
_CACHE = FEATURE_CACHE

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
    hints = history_hints(target_object, options)
    if use_cache:
        return _CACHE.get(shape_occ, options, hints=hints, **kwargs)
    from ..core.recognition import recognize
    return recognize(shape_occ, options, hints=hints, **kwargs)


def history_hints(target_object, options: Optional[RecognitionOptions] = None) -> list:
    """Hints from *target_object*'s history ([] without one, or when the
    preference to use them is off)."""
    options = options or read_options()
    if target_object is None or not options.use_history_hints:
        return []
    try:
        from .recognition_hints import hints_for
        return list(hints_for(target_object))
    except Exception:
        return []


def uses_recognition(process) -> bool:
    """True when an active rule of *process* is measured on recognised features."""
    from ..core.registries.analyzers_registry import get_analyzer_class
    from ..core.registries.checks_registry import get_check_class

    for rule in getattr(process, "active_rules", ()):
        check = get_check_class(rule)
        analyzer = get_analyzer_class(check().required_analyzer_id) if check else None
        if analyzer is not None and getattr(analyzer, "uses_recognition", False):
            return True
    return False


def features_for_object(obj, options: Optional[RecognitionOptions] = None, **kwargs):
    """(shape_occ, result) for a FreeCAD object with a Shape."""
    from ..core.utils.conversion import freecad_to_ocp

    shape_occ = freecad_to_ocp(obj.Shape)
    return shape_occ, features_for(shape_occ, obj, options, **kwargs)


def clear_cache() -> None:
    _CACHE.clear()
