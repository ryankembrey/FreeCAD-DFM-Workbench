from enum import Enum


class Rulebook(Enum):
    """
    A list of all DFM rule types supported by the workbench.
    - The Python code uses the Enum members (e.g. Rulebook.MIN_DRAFT_ANGLE)
    - The YAML process files use the Enum's string value (e.g. "MIN_DRAFT_ANGLE")
    """

    MIN_DRAFT_ANGLE = "MIN_DRAFT_ANGLE"
    MIN_WALL_THICKNESS = "MIN_WALL_THICKNESS"
    MAX_WALL_THICKNESS = "MAX_WALL_THICKNESS"
