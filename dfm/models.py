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
    message: str
    failing_geometry: List[TopoDS_Face]
    ignore: bool
