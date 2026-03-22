import csv
import base64

from PySide6 import QtCore, QtGui

import FreeCAD as App  # type: ignore

from gui.results.models import DFMReportModel


class CSVResultExporter:
    @staticmethod
    def export(filepath: str, target_label: str, model: DFMReportModel):
        try:
            with open(filepath, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                verdict_text, _ = model.get_verdict()

                # Metadata
                writer.writerow(["Design", target_label])
                writer.writerow(["Process", model.process.name])
                writer.writerow(["Material", model.material])
                writer.writerow(["Verdict", verdict_text])
                writer.writerow([])

                # Column Headers
                writer.writerow(
                    ["Status", "Rule Name", "Faces", "Value", "Comparison", "Limit", "Unit"]
                )

                for result in model.results:
                    if result.ignore:
                        continue

                    faces = "; ".join([ref.label for ref in result.refs])

                    # Write issue rows
                    writer.writerow(
                        [
                            result.severity.name,
                            result.rule_id.label,
                            faces,
                            result.value if result.value is not None else "N/A",
                            result.comparison,
                            result.limit if result.limit is not None else "N/A",
                            result.unit,
                        ]
                    )

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
    img = f"<img src='data:image/png;base64,{b64}' width='{size}' height='{size}'/>"
    return img
