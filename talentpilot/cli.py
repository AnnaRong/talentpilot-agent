from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from . import db, orchestrator
from .config import settings
from .runtime import Runtime


def main() -> int:
    parser = argparse.ArgumentParser(prog="talentpilot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default=settings.host)
    serve.add_argument("--port", type=int, default=settings.port)
    sub.add_parser("doctor")
    audit = sub.add_parser("audit")
    audit.add_argument("--input", required=True)
    audit.add_argument("--output-dir", default="data/reports")
    split = sub.add_parser("split")
    split.add_argument("--input", required=True)
    split.add_argument("--output-dir", default="data/recommendation-plan")
    args = parser.parse_args()
    if args.command == "init":
        db.init_db()
        runtime = Runtime(settings.data_dir / "platform")
        print(json.dumps({"ok": True, "db": str(settings.db_path),
                          "authentication": "disabled_design_mode"}, ensure_ascii=False))
        return 0
    if args.command == "doctor":
        db.init_db()
        print(json.dumps({
            "ok": True,
            "agent": "iTalentPilot Agent",
            "python": "3.10+ required",
            "db": str(settings.db_path),
            "model_key_configured": bool(settings.llm_api_key),
            "configured_model": settings.llm_model,
            "default_audit_model_calls": 0,
            "live_connectors_verified": False,
            "runtime": Runtime(settings.data_dir / "platform").capabilities(),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "serve":
        db.init_db()
        if args.host not in {"127.0.0.1", "localhost"}:
            parser.error("This release only supports loopback hosting; enterprise deployment is not configured")
        uvicorn.run("talentpilot.server:app", host=args.host, port=args.port, reload=False, access_log=False)
        return 0
    if args.command == "audit":
        settings.reports_dir = Path(args.output_dir).expanduser().resolve()
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        run_id = orchestrator.start({
            "kind": "audit",
            "goal": "CLI iTalent audit",
            "input_path": args.input,
        })
        result = orchestrator.wait_for_run(run_id)
        print(json.dumps({"run_id": run_id, "result": result}, ensure_ascii=False, indent=2))
        return 0 if result and result["status"] != "failed" else 1
    if args.command == "split":
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        candidates = data.get("candidates", data) if isinstance(data, dict) else data
        plan = orchestrator.build_batch_plan(candidates)
        output = Path(args.output_dir).expanduser()
        output.mkdir(parents=True, exist_ok=True)
        target = output / "recommendation-plan.json"
        target.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"path": str(target), "selected_count": plan["selected_count"]}, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
