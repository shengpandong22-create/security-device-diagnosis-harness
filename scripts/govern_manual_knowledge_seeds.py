"""显式导入或审核手工知识种子；两个动作必须使用不同 actor。"""

from __future__ import annotations

import argparse
import json

from security_diagnosis_harness.application.knowledge_seeds import (
    load_manual_knowledge_seeds,
)
from security_diagnosis_harness.config import RuntimeSettings
from security_diagnosis_harness.domain.knowledge import KnowledgeReviewAction
from security_diagnosis_harness.runtime import build_runtime_container


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    subparsers = parser.add_subparsers(dest="action", required=True)
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--manifest", required=True)
    import_parser.add_argument("--actor", required=True)
    review_parser = subparsers.add_parser("review")
    review_parser.add_argument("--knowledge-id", required=True)
    review_parser.add_argument("--reviewer", required=True)
    review_parser.add_argument(
        "--decision", choices=[item.value for item in KnowledgeReviewAction], required=True
    )
    review_parser.add_argument("--comment", default="")
    return parser


def main() -> int:
    args = _parser().parse_args()
    settings = RuntimeSettings(
        repository_mode="sqlite", database_url=args.database_url, auto_migrate=True
    )
    with build_runtime_container(settings) as runtime:
        if args.action == "import":
            imported = [
                runtime.knowledge_service.import_manual_seed(seed, args.actor)
                for seed in load_manual_knowledge_seeds(args.manifest)
            ]
            print(
                json.dumps(
                    {
                        "status": "candidate_review_required",
                        "knowledge_ids": [item.knowledge_id for item in imported],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        reviewed = runtime.knowledge_service.review(
            args.knowledge_id,
            KnowledgeReviewAction(args.decision),
            args.reviewer,
            args.comment,
        )
        print(
            json.dumps(
                {"knowledge_id": reviewed.knowledge_id, "status": reviewed.status.value},
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
