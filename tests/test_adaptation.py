import tempfile
import unittest
from pathlib import Path

from nlp_reading_level_adapter.repository import RecordRepository
from nlp_reading_level_adapter.workflow import TextWorkflow


GLOSSARY = [
    {"term_id": "person-zhang-qian", "canonical": "张骞", "category": "person", "aliases": []},
    {"term_id": "time-138-bc", "canonical": "公元前138年", "category": "time",
     "aliases": ["前138年"]},
    {"term_id": "domain-silk-road", "canonical": "丝绸之路", "category": "domain", "aliases": []},
    {"term_id": "domain-western-regions", "canonical": "西域", "category": "domain", "aliases": []},
]

V1 = {
    "record_id": "lesson-history", "source": "synthetic", "revision": 1,
    "observed_at": "2026-11-18T09:00:00Z",
    "text": "公元前138年，张骞奉汉武帝之命出使西域。这次行程打通了汉朝与西域各国的联系，因此被视为丝绸之路开通的开端。",
    "context": "历史课",
}
V2 = {
    **V1, "revision": 2, "observed_at": "2026-11-18T10:30:00Z",
    "text": "公元前138年，张骞奉汉武帝之命第一次出使西域。这次行程打通了汉朝与西域各国的联系，因此被视为丝绸之路开通的开端。",
}
GRADES = ["grade-3", "grade-5", "grade-7"]
ADAPTATION_ID = "lesson-history::grade-3+grade-5+grade-7"


def make_workflow(directory):
    return TextWorkflow(RecordRepository(Path(directory) / "records.json"))


def grade_view(view, grade):
    return next(item for item in view["grades"] if item["grade"] == grade)


def fragment(view, grade, sentence_id):
    return next(
        fragment for fragment in grade_view(view, grade)["fragments"]
        if fragment["sentence_id"] == sentence_id
    )


class AdaptationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workflow = make_workflow(self.temp.name)
        self.workflow.ingest([V1])
        self.workflow.update_glossary(GLOSSARY)
        self.view = self.workflow.create_adaptation("lesson-history", GRADES)

    def test_sentences_get_stable_ids_and_fragments_keep_source_evidence(self):
        record = self.workflow.list_records()[0]
        self.assertEqual([item["sentence_id"] for item in record["sentences"]], ["s1", "s2"])

        first = fragment(self.view, "grade-3", "s1")
        self.assertEqual(first["source_snapshot"]["source_text"], V1["text"].split("。")[0] + "。")
        self.assertIn("，", first["source_snapshot"]["source_text"])
        mention_ids = {item["term_id"] for item in first["mentions"]}
        self.assertEqual(
            mention_ids, {"time-138-bc", "person-zhang-qian", "domain-western-regions"}
        )
        self.assertTrue(all(item["provenance"] == "source" for item in first["mentions"]))
        self.assertEqual(first["glossary_version"], 1)
        self.assertEqual(first["status"], "current")
        self.assertEqual(first["stale_reason"], "")

    def test_simplification_lowers_sentence_difficulty_but_preserves_terms(self):
        grade3_s2 = fragment(self.view, "grade-3", "s2")
        # 书面连接词在低年级被替换，长句被拆短
        self.assertIn("所以", grade3_s2["adapted_text"])
        self.assertNotIn("因此", grade3_s2["adapted_text"])
        self.assertIn("丝绸之路", grade3_s2["adapted_text"])
        self.assertIn("西域", grade3_s2["adapted_text"])
        self.assertTrue(grade3_s2["adapted_text"].count("。") >= 2)

        grade7_s2 = fragment(self.view, "grade-7", "s2")
        # 高年级保留原句式，不替换连接词
        self.assertIn("因此", grade7_s2["adapted_text"])

        grade3_s1 = fragment(self.view, "grade-3", "s1")
        self.assertTrue(grade3_s1["adapted_text"].startswith("公元前138年。"))
        self.assertIn("张骞", grade3_s1["adapted_text"])

    def test_glossary_bumps_only_when_changed_and_only_affects_later_processing(self):
        unchanged = self.workflow.update_glossary(GLOSSARY)
        self.assertEqual(unchanged, {"version": 1, "changed": False, "terms": 4})

        with_new_term = self.workflow.update_glossary(
            GLOSSARY + [{"term_id": "person-han-wudi", "canonical": "汉武帝",
                         "category": "person", "aliases": []}]
        )
        self.assertEqual(with_new_term["version"], 2)

        # 已生成片段仍记录旧版本，状态可用但出现术语版本漂移提示
        view = self.workflow.get_adaptation("lesson-history", GRADES)
        self.assertTrue(grade_view(view, "grade-3")["glossary_drift"])
        self.assertEqual(fragment(view, "grade-3", "s1")["glossary_version"], 1)

        # 重新处理后未确认片段采用新版本
        result = self.workflow.reprocess(ADAPTATION_ID, only_stale=False)
        self.assertEqual(len(result["rebuilt"]), 6)
        view = self.workflow.get_adaptation("lesson-history", GRADES)
        self.assertFalse(grade_view(view, "grade-3")["glossary_drift"])
        self.assertEqual(fragment(view, "grade-3", "s1")["glossary_version"], 2)

    def test_teacher_revision_is_tracked_and_confirmed_expression_is_protected(self):
        revised = self.workflow.revise_fragment(
            ADAPTATION_ID, "s1", "grade-3",
            "公元前138年。张骞奉命远行。", "王老师"
        )
        self.assertEqual(revised["provenance"], "teacher")
        self.assertIn("原文术语缺失：西域", revised["warnings"])
        with self.assertRaises(ValueError):
            # 自动生成的片段不能直接确认
            self.workflow.confirm_fragment(ADAPTATION_ID, "s2", "grade-3", "王老师")

        self.workflow.confirm_fragment(ADAPTATION_ID, "s1", "grade-3", "王老师")
        # 教师新增术语会被标为 teacher 来源并提示核对
        with_added = self.workflow.revise_fragment(
            ADAPTATION_ID, "s1", "grade-5",
            "公元前138年，张骞出使西域，后来形成丝绸之路。", "王老师"
        )
        teacher_mentions = [m for m in with_added["mentions"] if m["provenance"] == "teacher"]
        self.assertEqual([m["term_id"] for m in teacher_mentions], ["domain-silk-road"])
        self.assertTrue(any("教师新增术语需核对" in w for w in with_added["warnings"]))

        # 原文修订后，已确认片段过期；批处理必须跳过、不得静默覆盖
        self.workflow.ingest([V2])
        result = self.workflow.reprocess(ADAPTATION_ID)
        self.assertEqual(result["skipped_confirmed"], [{"sentence_id": "s1", "grade": "grade-3"}])
        rebuilt_ids = {(item["sentence_id"], item["grade"]) for item in result["rebuilt"]}
        self.assertNotIn(("s1", "grade-3"), rebuilt_ids)
        view = self.workflow.get_adaptation("lesson-history", GRADES)
        still_revised = fragment(view, "grade-3", "s1")
        self.assertEqual(still_revised["adapted_text"], "公元前138年。张骞奉命远行。")
        self.assertTrue(still_revised["confirmed"])
        self.assertEqual(still_revised["status"], "stale")

    def test_source_change_marks_only_affected_levels_stale_and_locates_them(self):
        self.workflow.ingest([V2])
        view = self.workflow.get_adaptation("lesson-history", GRADES)

        s1_grades = {grade["grade"]: grade for grade in view["grades"]}
        for grade in GRADES:
            self.assertEqual(s1_grades[grade]["status"], "stale")
            self.assertEqual(s1_grades[grade]["stale_sentence_ids"], ["s1"])
            self.assertEqual(fragment(view, grade, "s1")["stale_reason"], "原句内容已修改")
            # 未受影响的层级仍可使用
            self.assertEqual(fragment(view, grade, "s2")["status"], "current")

        changes = self.workflow.locate_changes(ADAPTATION_ID)
        self.assertEqual(len(changes["changes"]), 1)
        change = changes["changes"][0]
        self.assertEqual(change["sentence_id"], "s1")
        self.assertEqual(change["kind"], "changed")
        self.assertIn("第一次", change["new_text"])
        self.assertEqual(change["affected_grades"], GRADES)

        # 教师复核后可解除过期，未受影响片段无需处理
        self.workflow.resolve_fragment(ADAPTATION_ID, "s1", "grade-3", "王老师", "已核对时间与人名")
        view = self.workflow.get_adaptation("lesson-history", GRADES)
        self.assertEqual(fragment(view, "grade-3", "s1")["status"], "current")
        self.assertEqual(grade_view(view, "grade-3")["status"], "current")

        self.workflow.clear_changes("lesson-history")
        self.assertEqual(self.workflow.locate_changes(ADAPTATION_ID)["changes"], [])

    def test_removed_sentence_blocks_reprocessing_and_moved_sentence_stays_valid(self):
        two_sentences = {
            "record_id": "lesson-move", "source": "synthetic", "revision": 1,
            "observed_at": "2026-11-18T09:00:00Z",
            "text": "张骞出使西域。丝绸之路连接东西方。",
        }
        removed = {**two_sentences, "revision": 2, "text": "丝绸之路连接东西方。"}
        moved = {**two_sentences, "revision": 2,
                 "text": "丝绸之路连接东西方。张骞出使西域。"}

        for revision, expected_kind in ((removed, "removed"), (moved, "moved")):
            with tempfile.TemporaryDirectory() as directory:
                workflow = make_workflow(directory)
                workflow.ingest([two_sentences])
                workflow.update_glossary(GLOSSARY)
                workflow.create_adaptation("lesson-move", ["grade-3"])
                workflow.ingest([revision])
                changes = workflow.locate_changes("lesson-move::grade-3")
                kinds = {change["sentence_id"]: change["kind"] for change in changes["changes"]}
                if expected_kind == "removed":
                    # 后句前移记为 moved，被删除的首句记为 removed
                    self.assertEqual(kinds, {"s1": "removed", "s2": "moved"})
                    view = workflow.get_adaptation("lesson-move", ["grade-3"])
                    s1 = fragment(view, "grade-3", "s1")
                    self.assertEqual(s1["status"], "stale")
                    self.assertEqual(s1["stale_reason"], "原句已移除")
                    result = workflow.reprocess("lesson-move::grade-3")
                    self.assertEqual(result["blocked_missing"], [{"sentence_id": "s1", "grade": "grade-3"}])
                    self.assertEqual(result["rebuilt"], [])
                else:
                    self.assertEqual(kinds, {"s1": "moved", "s2": "moved"})
                    # 仅位置移动、内容指纹不变：旧改写继续有效
                    view = workflow.get_adaptation("lesson-move", ["grade-3"])
                    self.assertEqual(fragment(view, "grade-3", "s1")["status"], "current")
                    self.assertEqual(fragment(view, "grade-3", "s2")["status"], "current")

    def test_history_records_every_manual_and_batch_action(self):
        self.workflow.revise_fragment(
            ADAPTATION_ID, "s1", "grade-3", "公元前138年。张骞远行。", "王老师"
        )
        self.workflow.confirm_fragment(ADAPTATION_ID, "s1", "grade-3", "王老师")
        self.workflow.ingest([V2])
        self.workflow.reprocess(ADAPTATION_ID)
        view = self.workflow.get_adaptation("lesson-history", GRADES)
        actions = [entry["action"] for entry in view["history"]]
        self.assertEqual(actions, ["create", "revise", "confirm", "reprocess"])
        revise_entry = next(entry for entry in view["history"] if entry["action"] == "revise")
        self.assertEqual(revise_entry["reviewer"], "王老师")
        self.assertEqual(revise_entry["grade"], "grade-3")

    def test_invalid_glossary_and_grades_are_rejected(self):
        with self.assertRaises(ValueError):
            self.workflow.update_glossary([{"term_id": "x", "canonical": "X", "category": "unknown"}])
        with self.assertRaises(ValueError):
            self.workflow.create_adaptation("lesson-history", ["grade-9"])
        with self.assertRaises(ValueError):
            self.workflow.create_adaptation("missing-record", ["grade-3"])


if __name__ == "__main__":
    unittest.main()
