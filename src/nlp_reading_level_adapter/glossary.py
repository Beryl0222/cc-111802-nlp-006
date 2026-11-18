from dataclasses import dataclass


TERM_CATEGORIES = ("person", "time", "domain")


@dataclass(frozen=True)
class GlossaryTerm:
    term_id: str
    canonical: str
    category: str
    aliases: tuple = ()
    glossary_version: int = 1
    note: str = ""

    @classmethod
    def from_dict(cls, value):
        term = cls(
            term_id=str(value.get("term_id", "")).strip(),
            canonical=str(value.get("canonical", "")).strip(),
            category=str(value.get("category", "")).strip(),
            aliases=tuple(str(alias).strip() for alias in value.get("aliases", []) if str(alias).strip()),
            glossary_version=int(value.get("glossary_version", 1)),
            note=str(value.get("note", "")).strip(),
        )
        if not term.term_id or not term.canonical:
            raise ValueError("术语缺少 term_id 或 canonical")
        if term.category not in TERM_CATEGORIES:
            raise ValueError(f"术语类别必须是 {TERM_CATEGORIES} 之一：{term.term_id}")
        if term.glossary_version < 1:
            raise ValueError("术语版本号必须从 1 开始")
        return term

    def to_dict(self):
        return {
            "term_id": self.term_id,
            "canonical": self.canonical,
            "category": self.category,
            "aliases": list(self.aliases),
            "glossary_version": self.glossary_version,
            "note": self.note,
        }


@dataclass(frozen=True)
class Glossary:
    version: int
    terms: tuple

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("术语表格式无效")
        version = int(value.get("version", 1))
        if version < 1:
            raise ValueError("术语表版本号必须从 1 开始")
        terms = tuple(GlossaryTerm.from_dict(term) for term in value.get("terms", []))
        ids = [term.term_id for term in terms]
        if len(ids) != len(set(ids)):
            raise ValueError("术语表中 term_id 不得重复")
        return cls(version=version, terms=terms)

    def to_dict(self):
        return {"version": self.version, "terms": [term.to_dict() for term in self.terms]}

    def find(self, term_id):
        for term in self.terms:
            if term.term_id == term_id:
                return term
        return None
