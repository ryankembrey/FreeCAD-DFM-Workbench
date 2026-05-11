# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import csv
import base64
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6 import QtCore, QtGui
import FreeCAD as App  # type: ignore

from ...core.models import CheckResult, Severity
from ...gui.results.models import DFMReportModel
from ...core.rules import Criticality


@dataclass
class CSVExportConfig:
    include_criticality: bool = True
    include_feedback: bool = True
    include_metadata: bool = True
    include_unit: bool = True
    include_errors: bool = True
    include_warnings: bool = True
    include_passed: bool = False
    include_ignored: bool = False
    delimiter: str = ","


class CSVResultExporter:
    DELIMITER_MAP = {
        0: ",",
        1: ";",
        2: "\t",
    }

    @staticmethod
    def export(
        filepath: str,
        target_label: str,
        model: DFMReportModel,
        config: CSVExportConfig,
        get_criticality: Optional[Callable] = None,
    ) -> bool:
        try:
            with open(filepath, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter=config.delimiter)

                if config.include_metadata:
                    verdict_text, _ = model.get_verdict()
                    writer.writerow(["Design", target_label])
                    writer.writerow(["Process", model.process.name])
                    writer.writerow(["Material", model.material])
                    writer.writerow(["Verdict", verdict_text])
                    writer.writerow([])

                headers = ["Status", "Rule"]
                if config.include_criticality:
                    headers.append("Criticality")
                headers += ["Face", "Value", "Comparison", "Limit"]
                if config.include_unit:
                    headers.append("Unit")
                if config.include_feedback:
                    headers.append("Message")
                writer.writerow(headers)

                grouped = model.get_grouped_results()

                def _crit_sort(item):
                    rule_id, _ = item
                    if get_criticality:
                        return get_criticality(rule_id).value
                    return Criticality.MEDIUM.value

                for rule_id, findings in sorted(grouped.items(), key=_crit_sort):
                    criticality_label = ""
                    if config.include_criticality and get_criticality:
                        criticality_label = get_criticality(rule_id).label

                    feedback = model.process.rule_feedback.get(rule_id)

                    for result in findings:
                        if result.ignore and not config.include_ignored:
                            continue
                        if result.severity == Severity.ERROR and not config.include_errors:
                            continue
                        if result.severity == Severity.WARNING and not config.include_warnings:
                            continue

                        face = "; ".join(ref.label for ref in result.refs)

                        if result.severity == Severity.ERROR:
                            msg = feedback.error_msg if feedback else ""
                        else:
                            msg = feedback.warning_msg if feedback else ""

                        row = [
                            ("~~" if result.ignore else "") + result.severity.name,
                            rule_id.label,
                        ]
                        if config.include_criticality:
                            row.append(criticality_label)
                        row += [
                            face,
                            f"{result.value:.2f}" if result.value is not None else "N/A",
                            result.comparison,
                            result.limit if result.limit is not None else "N/A",
                        ]
                        if config.include_unit:
                            row.append(result.unit)
                        if config.include_feedback:
                            row.append(msg)

                        writer.writerow(row)

                if config.include_passed:
                    grouped_rule_ids = set(grouped.keys())
                    passed = [r for r in model.process.active_rules if r not in grouped_rule_ids]
                    for rule in passed:
                        row = ["PASS", rule.label]
                        if config.include_criticality:
                            crit = get_criticality(rule).label if get_criticality else ""
                            row.append(crit)
                        row += ["", "N/A", "", "N/A"]
                        if config.include_unit:
                            row.append("")
                        if config.include_feedback:
                            row.append("")
                        writer.writerow(row)

            return True

        except Exception as e:
            App.Console.PrintError(f"Export failed: {e}\n")
            return False


def icon_to_html(icon: QtGui.QIcon, size: int = 16) -> str:
    pixmap = icon.pixmap(size, size)
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    b64 = base64.b64encode(buffer.data().data()).decode()
    return f"<img src='data:image/png;base64,{b64}' width='{size}' height='{size}'/>"
