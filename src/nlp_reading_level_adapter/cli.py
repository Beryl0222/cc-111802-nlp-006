import argparse
import json
from pathlib import Path

from .repository import RecordRepository
from .workflow import TextWorkflow


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _adaptation_id(args):
    return f"{args.record_id}::" + "+".join(args.grades)


def build_parser():
    parser = argparse.ArgumentParser(description="本地分层阅读改写工具")
    parser.add_argument("--state", default="var/records.json")
    sub = parser.add_subparsers(dest="action", required=True)

    ingest = sub.add_parser("ingest", help="导入/更新原文记录")
    ingest.add_argument("path", nargs="?", default="fixtures/records.json")
    ingest.add_argument("--source")

    sub.add_parser("list", help="查看原文记录")

    glossary_import = sub.add_parser("glossary-import", help="整体替换术语表（内容变化才升版本）")
    glossary_import.add_argument("path")
    sub.add_parser("glossary-list", help="查看当前术语表与版本")

    create = sub.add_parser("adapt", help="按年级生成分层改写")
    create.add_argument("record_id")
    create.add_argument("grades", nargs="+")

    view = sub.add_parser("view", help="按年级查看改写内容、依据与状态")
    view.add_argument("record_id")
    view.add_argument("grades", nargs="+")

    revise = sub.add_parser("revise", help="教师手工修订某句某年级的改写")
    revise.add_argument("record_id")
    revise.add_argument("grades", nargs="+")
    revise.add_argument("--sentence", required=True)
    revise.add_argument("--grade", required=True)
    revise.add_argument("--text", required=True)
    revise.add_argument("--reviewer", required=True)

    confirm = sub.add_parser("confirm", help="确认教师手工修订表达（批处理不得覆盖）")
    confirm.add_argument("record_id")
    confirm.add_argument("grades", nargs="+")
    confirm.add_argument("--sentence", required=True)
    confirm.add_argument("--grade", required=True)
    confirm.add_argument("--reviewer", required=True)

    resolve = sub.add_parser("resolve", help="复核后清除片段过期状态")
    resolve.add_argument("record_id")
    resolve.add_argument("grades", nargs="+")
    resolve.add_argument("--sentence", required=True)
    resolve.add_argument("--grade", required=True)
    resolve.add_argument("--reviewer", required=True)
    resolve.add_argument("--note", default="")

    changes = sub.add_parser("changes", help="定位改变的原句及受影响年级")
    changes.add_argument("record_id")
    changes.add_argument("grades", nargs="+")

    reprocess = sub.add_parser("reprocess", help="批处理重建片段（跳过已确认）")
    reprocess.add_argument("record_id")
    reprocess.add_argument("grades", nargs="+")
    reprocess.add_argument("--all", action="store_true", help="不只处理过期片段")

    clear = sub.add_parser("clear-changes", help="清除待处理变更清单")
    clear.add_argument("record_id")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    workflow = TextWorkflow(RecordRepository(args.state))

    if args.action == "ingest":
        result = workflow.ingest(_load(args.path))
    elif args.action == "list":
        result = workflow.list_records(getattr(args, "source", None))
    elif args.action == "glossary-import":
        result = workflow.update_glossary(_load(args.path))
    elif args.action == "glossary-list":
        result = workflow.list_glossary()
    elif args.action == "adapt":
        result = workflow.create_adaptation(args.record_id, args.grades)
    elif args.action == "view":
        result = workflow.get_adaptation(args.record_id, args.grades)
    elif args.action == "revise":
        result = workflow.revise_fragment(
            _adaptation_id(args), args.sentence, args.grade, args.text, args.reviewer
        )
    elif args.action == "confirm":
        result = workflow.confirm_fragment(
            _adaptation_id(args), args.sentence, args.grade, args.reviewer
        )
    elif args.action == "resolve":
        result = workflow.resolve_fragment(
            _adaptation_id(args), args.sentence, args.grade, args.reviewer, args.note
        )
    elif args.action == "changes":
        result = workflow.locate_changes(_adaptation_id(args))
    elif args.action == "reprocess":
        result = workflow.reprocess(_adaptation_id(args), only_stale=not args.all)
    elif args.action == "clear-changes":
        result = workflow.clear_changes(args.record_id)
    else:  # pragma: no cover
        parser.error(f"未知动作：{args.action}")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
