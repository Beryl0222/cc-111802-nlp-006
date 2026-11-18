from datetime import datetime, timezone

from .adaptation import (
    GRADE_PROFILES,
    adaptation_view,
    build_adaptation,
    build_fragment,
    locate_changes,
    regenerate_fragment,
)
from .glossary import Glossary, GlossaryTerm
from .records import TextRecord
from .sentences import align_sentences, build_sentences
from .text import analyze_mentions, annotate, normalize_text


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TextWorkflow:
    def __init__(self, repository):
        self.repository = repository

    # ---------- 内部状态 ----------

    def _state(self):
        state = self.repository.read_state()
        state.setdefault("records", [])
        state.setdefault("glossary", {"version": 1, "terms": []})
        state.setdefault("adaptations", [])
        return state

    def _glossary(self, state):
        return Glossary.from_dict(state["glossary"])

    def _find_record(self, state, record_id):
        for record in state["records"]:
            if record["record_id"] == record_id:
                return record
        return None

    def _find_adaptation(self, state, adaptation_id):
        for adaptation in state["adaptations"]:
            if adaptation["adaptation_id"] == adaptation_id:
                return adaptation
        return None

    def _sentence(self, record, sentence_id):
        for sentence in record["sentences"]:
            if sentence["sentence_id"] == sentence_id:
                return sentence
        raise ValueError(f"原句不存在：{sentence_id}")

    def _fragment(self, adaptation, sentence_id, grade):
        for fragment in adaptation["fragments"]:
            if fragment["sentence_id"] == sentence_id and fragment["grade"] == grade:
                return fragment
        raise ValueError(f"改写片段不存在：句子 {sentence_id} / 年级 {grade}")

    def _append_history(self, adaptation, action, **payload):
        adaptation["history"].append({"at": _utc_now(), "action": action, **payload})

    # ---------- 原文导入 ----------

    def ingest(self, values):
        if not isinstance(values, list):
            raise TypeError("导入内容必须是记录列表")
        incoming = [TextRecord.from_dict(value).to_dict() for value in values]
        state = self._state()
        current = {record["record_id"]: record for record in state["records"]}

        for record in incoming:
            previous = current.get(record["record_id"])
            normalized = normalize_text(record["text"])
            record["text"] = normalized
            record["analysis"] = annotate(record)

            if previous is None:
                record["sentences"] = build_sentences(normalized)
                record["pending_changes"] = [
                    {"sentence_id": sentence["sentence_id"], "kind": "added",
                     "old_index": None, "new_index": sentence["index"],
                     "old_text": None, "new_text": sentence["text"]}
                    for sentence in record["sentences"]
                ]
            elif record["revision"] >= previous["revision"]:
                if record["revision"] == previous["revision"] and record["text"] == previous["text"]:
                    continue
                sentences, changes = align_sentences(previous["sentences"], normalized)
                record["sentences"] = sentences
                record["pending_changes"] = changes
            else:
                continue
            current[record["record_id"]] = record

        state["records"] = sorted(current.values(), key=lambda row: row["record_id"])
        self.repository.write_state(state)
        return {"received": len(incoming), "stored": len(state["records"])}

    def list_records(self, source=None):
        state = self._state()
        records = state["records"]
        if source:
            records = [record for record in records if record["source"] == source]
        return records

    # ---------- 术语表 ----------

    def update_glossary(self, values):
        """术语表整体替换：内容确有变化才递增版本；版本号会写进后续处理的片段中。"""
        if not isinstance(values, list):
            raise TypeError("术语内容必须是术语列表")
        state = self._state()
        previous = self._glossary(state)
        candidate = Glossary.from_dict(
            {"version": previous.version, "terms": values}
        )

        def fingerprint(term):
            return (term.term_id, term.canonical, term.category, term.aliases, term.note)

        if [fingerprint(term) for term in candidate.terms] == \
                [fingerprint(term) for term in previous.terms]:
            return {"version": previous.version, "changed": False, "terms": len(previous.terms)}

        # 首次导入（空术语表）保留版本 1；之后内容变化才递增版本
        new_version = previous.version if not previous.terms else previous.version + 1
        bumped = tuple(
            GlossaryTerm(
                term_id=term.term_id,
                canonical=term.canonical,
                category=term.category,
                aliases=term.aliases,
                glossary_version=new_version,
                note=term.note,
            )
            for term in candidate.terms
        )
        state["glossary"] = Glossary(version=new_version, terms=bumped).to_dict()
        self.repository.write_state(state)
        return {"version": new_version, "changed": True, "terms": len(bumped)}

    def list_glossary(self):
        return self._glossary(self._state()).to_dict()

    # ---------- 分层改写 ----------

    def create_adaptation(self, record_id, grades):
        grades = list(grades)
        if not grades:
            raise ValueError("至少选择一个年级")
        for grade in grades:
            if grade not in GRADE_PROFILES:
                raise ValueError(f"未知年级：{grade}")
        state = self._state()
        record = self._find_record(state, record_id)
        if record is None:
            raise ValueError(f"原文记录不存在：{record_id}")
        adaptation_id = f"{record_id}::" + "+".join(grades)
        if self._find_adaptation(state, adaptation_id) is not None:
            raise ValueError(f"分层改写已存在：{adaptation_id}")
        adaptation = build_adaptation(record, grades, self._glossary(state))
        self._append_history(
            adaptation, "create",
            glossary_version=self._glossary(state).version,
            grades=grades,
        )
        state["adaptations"].append(adaptation)
        self.repository.write_state(state)
        return self.get_adaptation(record_id, grades)

    def get_adaptation(self, record_id, grades):
        state = self._state()
        record = self._find_record(state, record_id)
        if record is None:
            raise ValueError(f"原文记录不存在：{record_id}")
        adaptation_id = f"{record_id}::" + "+".join(grades)
        adaptation = self._find_adaptation(state, adaptation_id)
        if adaptation is None:
            raise ValueError(f"分层改写不存在：{adaptation_id}")
        return adaptation_view(adaptation, record, self._glossary(state).version)

    def list_adaptations(self, record_id=None, grade=None):
        state = self._state()
        glossary_version = self._glossary(state).version
        views = []
        for adaptation in state["adaptations"]:
            if record_id and adaptation["record_id"] != record_id:
                continue
            if grade and grade not in adaptation["grades"]:
                continue
            record = self._find_record(state, adaptation["record_id"])
            if record is None:
                continue
            views.append(adaptation_view(adaptation, record, glossary_version))
        return views

    # ---------- 教师手工操作（不会被批处理静默覆盖） ----------

    def revise_fragment(self, adaptation_id, sentence_id, grade, adapted_text, reviewer):
        adapted_text = normalize_text(adapted_text)
        if not adapted_text:
            raise ValueError("改写文本不能为空")
        state = self._state()
        adaptation = self._find_adaptation(state, adaptation_id)
        if adaptation is None:
            raise ValueError(f"分层改写不存在：{adaptation_id}")
        record = self._find_record(state, adaptation["record_id"])
        sentence = self._sentence(record, sentence_id)
        fragment = self._fragment(adaptation, sentence_id, grade)
        glossary = self._glossary(state)

        previous_text = fragment["adapted_text"]
        mentions, warnings = analyze_mentions(
            sentence["text"], adapted_text, glossary.terms, glossary.version
        )
        fragment["adapted_text"] = adapted_text
        fragment["provenance"] = "teacher"
        fragment["mentions"] = mentions
        fragment["warnings"] = warnings
        # 教师修订不改动 confirmed / glossary_version：确认状态只由显式确认动作改变，
        # 术语版本依据保留在片段上，待重新处理时才刷新。
        self._append_history(
            adaptation, "revise",
            sentence_id=sentence_id, grade=grade, reviewer=reviewer,
            previous_text=previous_text, new_text=adapted_text,
        )
        self.repository.write_state(state)
        return self._fragment(adaptation, sentence_id, grade)

    def confirm_fragment(self, adaptation_id, sentence_id, grade, reviewer):
        state = self._state()
        adaptation = self._find_adaptation(state, adaptation_id)
        if adaptation is None:
            raise ValueError(f"分层改写不存在：{adaptation_id}")
        fragment = self._fragment(adaptation, sentence_id, grade)
        if fragment["provenance"] != "teacher":
            raise ValueError("仅可确认教师手工修订过的表达")
        fragment["confirmed"] = True
        fragment["confirmed_by"] = reviewer
        fragment["confirmed_at"] = _utc_now()
        self._append_history(
            adaptation, "confirm",
            sentence_id=sentence_id, grade=grade, reviewer=reviewer,
        )
        self.repository.write_state(state)
        return fragment

    def resolve_fragment(self, adaptation_id, sentence_id, grade, reviewer, note=""):
        """教师完成复核后清除过期标记（快照刷新为当前原句，保留现有改写文本）。"""
        state = self._state()
        adaptation = self._find_adaptation(state, adaptation_id)
        if adaptation is None:
            raise ValueError(f"分层改写不存在：{adaptation_id}")
        record = self._find_record(state, adaptation["record_id"])
        sentence = self._sentence(record, sentence_id)
        fragment = self._fragment(adaptation, sentence_id, grade)
        glossary = self._glossary(state)
        fragment["source_snapshot"] = {
            "source_index": sentence["index"],
            "source_text": sentence["text"],
            "source_hash": sentence["hash"],
        }
        mentions, warnings = analyze_mentions(
            sentence["text"], fragment["adapted_text"], glossary.terms, glossary.version
        )
        fragment["mentions"] = mentions
        fragment["warnings"] = warnings
        self._append_history(
            adaptation, "resolve",
            sentence_id=sentence_id, grade=grade, reviewer=reviewer, note=note,
        )
        self.repository.write_state(state)
        return fragment

    def clear_changes(self, record_id):
        """教师已定位并处理完本轮变更后，清除待处理变更清单。"""
        state = self._state()
        record = self._find_record(state, record_id)
        if record is None:
            raise ValueError(f"原文记录不存在：{record_id}")
        record["pending_changes"] = []
        self.repository.write_state(state)
        return {"record_id": record_id, "cleared": True}

    # ---------- 批处理：术语表更新只作用于后续处理，确认内容受保护 ----------

    def reprocess(self, adaptation_id, only_stale=True):
        state = self._state()
        adaptation = self._find_adaptation(state, adaptation_id)
        if adaptation is None:
            raise ValueError(f"分层改写不存在：{adaptation_id}")
        record = self._find_record(state, adaptation["record_id"])
        glossary = self._glossary(state)
        sentence_lookup = {sentence["sentence_id"]: sentence for sentence in record["sentences"]}

        rebuilt, skipped_confirmed, blocked_missing = [], [], []
        for fragment in adaptation["fragments"]:
            sentence = sentence_lookup.get(fragment["sentence_id"])
            is_stale = sentence is None or sentence["hash"] != fragment["source_snapshot"]["source_hash"]
            if only_stale and not is_stale:
                continue
            if fragment["confirmed"]:
                skipped_confirmed.append({
                    "sentence_id": fragment["sentence_id"], "grade": fragment["grade"],
                })
                continue
            if sentence is None:
                blocked_missing.append({
                    "sentence_id": fragment["sentence_id"], "grade": fragment["grade"],
                })
                continue
            regenerate_fragment(fragment, sentence, glossary.terms, glossary.version)
            rebuilt.append({"sentence_id": fragment["sentence_id"], "grade": fragment["grade"]})

        self._append_history(
            adaptation, "reprocess",
            only_stale=only_stale, glossary_version=glossary.version,
            rebuilt=rebuilt, skipped_confirmed=skipped_confirmed,
            blocked_missing=blocked_missing,
        )
        self.repository.write_state(state)
        return {
            "rebuilt": rebuilt,
            "skipped_confirmed": skipped_confirmed,
            "blocked_missing": blocked_missing,
            "glossary_version": glossary.version,
        }

    def locate_changes(self, adaptation_id):
        state = self._state()
        adaptation = self._find_adaptation(state, adaptation_id)
        if adaptation is None:
            raise ValueError(f"分层改写不存在：{adaptation_id}")
        record = self._find_record(state, adaptation["record_id"])
        return locate_changes(adaptation, record)
