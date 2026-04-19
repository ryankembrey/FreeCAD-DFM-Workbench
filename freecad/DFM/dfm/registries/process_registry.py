# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the DFM addon.

#  ***************************************************************************
#  *   Copyright (c) 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>              *
#  *                                                                         *
#  *   This file is part of the FreeCAD CAx development system.              *
#  *                                                                         *
#  *   This library is free software; you can redistribute it and/or         *
#  *   modify it under the terms of the GNU Library General Public           *
#  *   License as published by the Free Software Foundation; either          *
#  *   version 2 of the License, or (at your option) any later version.      *
#  *                                                                         *
#  *   This library  is distributed in the hope that it will be useful,      *
#  *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#  *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#  *   GNU Library General Public License for more details.                  *
#  *                                                                         *
#  *   You should have received a copy of the GNU Library General Public     *
#  *   License along with this library; see the file COPYING.LIB. If not,    *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

import yaml
from pathlib import Path
from typing import Optional
from dataclasses import asdict

import FreeCAD as App  # type: ignore

from ...dfm.processes.process import Process
from ...dfm.rules import Rulebook


class ProcessRegistry:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        if self._instance is not None:
            raise RuntimeError("ProcessRegistry is a singleton, use get_instance()")

        self.processes: dict[str, Process] = {}

        self.dev_dir = Path(__file__).parent.parent / "processes"
        self.user_dir = Path(App.getUserAppDataDir()) / "dfm" / "processes"

        self.discover_processes()

    def discover_processes(self):
        """Scans both dev and user directories. User versions override Dev versions."""
        self.processes.clear()

        self._load_from_dir(self.dev_dir)

        self._load_from_dir(self.user_dir)

        print(f"Registry initialized: {len(self.processes)} processes loaded.")

    def _load_from_dir(self, directory: Path):
        """Helper to load all YAML files from a specific Path object."""
        if not directory.exists():
            return

        for filepath in directory.glob("*.yaml"):
            try:
                with open(filepath, "r") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        process = Process.from_yaml(data)
                        self.processes[process.name] = process
            except Exception as e:
                print(f"Error loading {filepath.name} from {directory}: {e}")

    def save_all_processes(self):
        """Saves all processes. Any changes or new items go to the User AppData dir."""
        if not self.user_dir.exists():
            self.user_dir.mkdir(parents=True, exist_ok=True)

        for process in self.processes.values():
            self._save_to_user_dir(process)

    def _save_to_user_dir(self, process: Process):
        """Serializes process and writes it specifically to the User directory."""
        serialized_data = self._serialize_process(process)

        filename = f"{process.name.lower().replace(' ', '_')}.yaml"
        filepath = self.user_dir / filename

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(
                    serialized_data,
                    f,
                    sort_keys=False,
                    default_flow_style=False,
                    indent=4,
                    allow_unicode=True,
                )
            print(f"Saved: {process.name} -> {filepath}")
        except Exception as e:
            print(f"Failed to save {process.name} to user dir: {e}")

    def _serialize_process(self, process: Process) -> dict:
        """Converts dataclasses and Enums into a YAML-compatible dict."""
        data = {
            "name": process.name,
            "category": process.category,
            "description": process.description,
            "active_rules": [r.name for r in process.active_rules],
            "rule_feedback": {r.name: asdict(f) for r, f in process.rule_feedback.items()},
            "materials": {},
        }

        for mat_name, mat in process.materials.items():
            mat_dict = {
                "category": mat.category,
                "is_active": mat.is_active,
                "rule_limits": {
                    rule.name: self._serialize_rule_limit(rule, limit)
                    for rule, limit in mat.rule_limits.items()
                },
            }
            data["materials"][mat_name] = mat_dict
        return data

    def get_categories(self) -> list[str]:
        categories = {p.category for p in self.processes.values()}
        return sorted(list(categories))

    def get_processes_for_category(self, category_name: str) -> list[Process]:
        return sorted(
            [p for p in self.processes.values() if p.category == category_name],
            key=lambda p: p.name,
        )

    def get_process_by_name(self, name: str) -> Optional[Process]:
        return self.processes.get(name)

    def add_process(self, process: Process):
        self.processes[process.name] = process

    def get_process_by_id(self, process_id: str) -> Process:
        return self.processes.get(process_id)  # type: ignore

    def _serialize_rule_limit(self, rule: Rulebook, limit) -> dict:
        if rule.is_binary:
            return {"binary_severity": limit.binary_severity}
        else:
            return {"target": limit.target, "limit": limit.limit}
