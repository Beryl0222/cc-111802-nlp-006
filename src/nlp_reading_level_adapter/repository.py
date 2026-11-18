import json
from pathlib import Path


class RecordRepository:
    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(value, dict) or not isinstance(value.get("records"), list):
            raise ValueError("记录库格式无效")
        return value

    def read(self):
        value = self._load()
        return [] if value is None else value["records"]

    def read_state(self):
        value = self._load()
        if value is None:
            return {"records": [], "glossary": {"version": 1, "terms": []}, "adaptations": []}
        if not isinstance(value.get("glossary"), dict) or not isinstance(value.get("adaptations"), list):
            raise ValueError("记录库格式无效")
        return {
            "records": value["records"],
            "glossary": value["glossary"],
            "adaptations": value["adaptations"],
        }

    def replace(self, records):
        state = self.read_state()
        state["records"] = records
        self.write_state(state)

    def write_state(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)
