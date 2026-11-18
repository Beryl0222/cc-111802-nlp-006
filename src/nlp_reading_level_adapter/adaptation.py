from .text import analyze_mentions, simplify_sentence


GRADE_PROFILES = {
    "grade-3": {"label": "三年级", "split_threshold": 18, "replace_connectives": True},
    "grade-5": {"label": "五年级", "split_threshold": 28, "replace_connectives": True},
    "grade-7": {"label": "七年级", "split_threshold": 45, "replace_connectives": False},
}


def build_fragment(sentence, grade, terms, glossary_version):
    """基于单条原句生成一个年级改写片段，并记录术语来源与依据快照。"""
    profile = GRADE_PROFILES[grade]
    adapted_text = simplify_sentence(sentence["text"], terms, profile)
    mentions, warnings = analyze_mentions(
        sentence["text"], adapted_text, terms, glossary_version
    )
    return {
        "sentence_id": sentence["sentence_id"],
        "grade": grade,
        "adapted_text": adapted_text,
        "provenance": "auto",
        "confirmed": False,
        "confirmed_by": None,
        "confirmed_at": None,
        "glossary_version": glossary_version,
        "source_snapshot": {
            "source_index": sentence["index"],
            "source_text": sentence["text"],
            "source_hash": sentence["hash"],
        },
        "mentions": mentions,
        "warnings": warnings,
    }


def build_adaptation(record, grades, glossary):
    fragments = []
    for sentence in record["sentences"]:
        for grade in grades:
            if grade not in GRADE_PROFILES:
                raise ValueError(f"未知年级：{grade}")
            fragments.append(build_fragment(sentence, grade, glossary.terms, glossary.version))
    return {
        "adaptation_id": f"{record['record_id']}::" + "+".join(grades),
        "record_id": record["record_id"],
        "grades": list(grades),
        "fragments": fragments,
        "history": [],
    }


def regenerate_fragment(fragment, sentence, terms, glossary_version):
    """批处理重建未确认片段；保留原片段身份，刷新文本、溯源与术语版本。"""
    profile = GRADE_PROFILES[fragment["grade"]]
    adapted_text = simplify_sentence(sentence["text"], terms, profile)
    mentions, warnings = analyze_mentions(
        sentence["text"], adapted_text, terms, glossary_version
    )
    fragment["adapted_text"] = adapted_text
    fragment["provenance"] = "auto"
    fragment["glossary_version"] = glossary_version
    fragment["source_snapshot"] = {
        "source_index": sentence["index"],
        "source_text": sentence["text"],
        "source_hash": sentence["hash"],
    }
    fragment["mentions"] = mentions
    fragment["warnings"] = warnings


def fragment_status(fragment, sentence_lookup):
    """动态计算片段状态：事实依据以原句哈希为准。"""
    sentence = sentence_lookup.get(fragment["sentence_id"])
    if sentence is None:
        return "stale", "原句已移除"
    if sentence["hash"] != fragment["source_snapshot"]["source_hash"]:
        return "stale", "原句内容已修改"
    return "current", ""


def enrich_fragment(fragment, sentence_lookup, glossary_version):
    status, reason = fragment_status(fragment, sentence_lookup)
    enriched = dict(fragment)
    enriched["status"] = status
    enriched["stale_reason"] = reason
    enriched["glossary_drift"] = (
        status == "current" and fragment["glossary_version"] != glossary_version
    )
    return enriched


def adaptation_view(adaptation, record, glossary_version):
    sentence_lookup = {sentence["sentence_id"]: sentence for sentence in record["sentences"]}
    grade_views = []
    for grade in adaptation["grades"]:
        fragments = [
            enrich_fragment(fragment, sentence_lookup, glossary_version)
            for fragment in adaptation["fragments"]
            if fragment["grade"] == grade
        ]
        stale = [fragment["sentence_id"] for fragment in fragments if fragment["status"] == "stale"]
        grade_views.append({
            "grade": grade,
            "grade_label": GRADE_PROFILES[grade]["label"],
            "status": "stale" if stale else "current",
            "stale_sentence_ids": stale,
            "glossary_drift": any(fragment["glossary_drift"] for fragment in fragments),
            "fragments": fragments,
        })
    return {
        "adaptation_id": adaptation["adaptation_id"],
        "record_id": adaptation["record_id"],
        "source_revision": record["revision"],
        "grades": grade_views,
        "history": adaptation["history"],
    }


def locate_changes(adaptation, record):
    """把本次原文变更定位到句子及受影响的年级，供教师决定是否重新复核。"""
    sentence_lookup = {sentence["sentence_id"]: sentence for sentence in record["sentences"]}
    grades = sorted({fragment["grade"] for fragment in adaptation["fragments"]})
    affected = []
    for change in record.get("pending_changes", []):
        affected_grades = sorted({
            fragment["grade"]
            for fragment in adaptation["fragments"]
            if fragment["sentence_id"] == change["sentence_id"]
        })
        current_sentence = sentence_lookup.get(change["sentence_id"])
        affected.append({
            "sentence_id": change["sentence_id"],
            "kind": change["kind"],
            "old_text": change["old_text"],
            "new_text": change["new_text"],
            "current_text": current_sentence["text"] if current_sentence else None,
            "affected_grades": affected_grades,
        })
    return {
        "record_id": record["record_id"],
        "revision": record["revision"],
        "all_grades": grades,
        "changes": affected,
    }
