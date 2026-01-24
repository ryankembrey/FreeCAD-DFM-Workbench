import os
import yaml

from dfm.processes.process import Process


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
        self.discover_processes()

    def discover_processes(self):
        """Scans the 'processes' directory for YAML files and loads them."""
        process_dir = os.path.join(os.path.dirname(__file__), "../processes")

        for filename in os.listdir(process_dir):
            if filename.endswith((".yaml", ".yml")):
                filepath = os.path.join(process_dir, filename)
                with open(filepath, "r") as f:
                    data = yaml.safe_load(f)
                    process = Process(**data)  # type: ignore
                    self.processes[process.name] = process
        print(f"Discovered {len(self.processes)} DFM processes.")

    def get_categories(self) -> list[str]:
        """Returns a sorted list of unique category names."""
        categories = set(p.category for p in self.processes.values())
        return sorted(list(categories))

    def get_processes_for_category(self, category_name: str) -> list[Process]:
        """Returns all process objects belonging to a specific category."""
        return sorted(
            [p for p in self.processes.values() if p.category == category_name],
            key=lambda p: p.name,
        )

    def get_process_by_id(self, process_id: str) -> Process:
        return self.processes.get(process_id)  # type: ignore
