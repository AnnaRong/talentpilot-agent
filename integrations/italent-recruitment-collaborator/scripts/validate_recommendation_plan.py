#!/usr/bin/env python3
"""Validate a local recommendation plan before handing it to baidu-italent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    data = json.loads(args.plan.read_text(encoding="utf-8"))
    errors: list[str] = []
    if data.get("schema") != "italent-recommendation-plan/v1":
        errors.append("unexpected schema")
    if data.get("selected_count") != 20:
        errors.append("selected_count must be 20 for the requested batch")
    groups = data.get("groups")
    if not isinstance(groups, list) or len(groups) != 2:
        errors.append("plan must contain exactly two groups")
    else:
        counts = [group.get("count") for group in groups]
        if counts != [8, 12]:
            errors.append(f"group counts must be [8, 12], got {counts}")
        seen: list[str] = []
        for group in groups:
            for member in group.get("members", []):
                candidate_id = member.get("candidate_id")
                if not candidate_id:
                    errors.append("member without candidate_id")
                seen.append(str(candidate_id))
                if member.get("status") != "needs_live_italent_context":
                    errors.append(f"{candidate_id}: unexpected status")
        if len(seen) != len(set(seen)):
            errors.append("duplicate candidate_id across groups")
    if errors:
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"valid": True, "selected_count": 20, "write_enabled": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
