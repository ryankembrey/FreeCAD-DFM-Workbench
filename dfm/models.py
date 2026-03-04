from dataclasses import dataclass
from typing import List
from OCC.Core.TopoDS import TopoDS_Face
from .rules import Rulebook

from enum import Enum, auto


class Severity(Enum):
    INFO = auto()
    WARNING = auto()
    ERROR = auto()


@dataclass
class CheckResult:
    """
    A data class to hold the result of a single DFM check finding.
    """

    rule_id: Rulebook
    severity: Severity
    overview: str
    message: str
    failing_geometry: List[TopoDS_Face]
    ignore: bool
    value: float
    limit: float
    comparison: str = ""
    unit: str = ""


class ProcessRequirement(Enum):
    PULL_DIRECTION = "PULL_DIRECTION"
    NONE = "NONE"

    @classmethod
    def from_str(cls, label: str):
        try:
            return cls[label.upper()]
        except KeyError:
            return cls.NONE
