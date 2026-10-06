from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import time
import uuid
from html import escape
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RecordingManager:
    """Owns auditable screen-recording sessions."""

    def __init__(self) -> None:
        self.root = settings.recordings_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[str, dict[str, Any]] = {}

    def start(self, task_id: str | None = None, label: str = "招聘操作录频") -> dict[str, Any]:
        recording_id = uuid.uuid4().hex
        session_dir = self.root / datetime.now().strftime("%Y-%m-%d") / recording_id
        session_dir.mkdir(parents=True, exist_ok=True)
        title = f"{_stamp()}-{label}"
        manifest_path = session_dir / f"{title}.json"
        events_path = session_dir / f"{title}.jsonl"
        video_path = session_dir / f"{title}.mov"
        stderr_path = session_dir / "recording-stderr.log"
        state: dict[str, Any] = {
            "id": recording_id,
            "task_id": task_id,
            "title": title,
            "status": "recording",
            "started_at": _iso_now(),
            "stopped_at": None,
            "directory": str(session_dir),
            "video_path": None,
            "events_path": str(events_path),
            "manifest_path": str(manifest_path),
            "replay_path": str(session_dir / f"{title}.html"),
            "frames": [],
            "backend": "event-replay",
            "process": None,
            "video_verified": False,
            "stderr_path": str(stderr_path),
        }
        events_path.write_text("", encoding="utf-8")
        if platform.system() == "Darwin" and os.getenv("TALENTPILOT_NATIVE_SCREEN_RECORDING", "0") == "1":
            try:
                with stderr_path.open("wb") as stderr_handle:
                    process = subprocess.Popen(
                        [
                            "/usr/sbin/screencapture",
                            "-x",
                            "-v",
                            "-V",
                            os.getenv("TALENTPILOT_RECORDING_MAX_SECONDS", "3600"),
                            str(video_path),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=stderr_handle,
                    )
                state["process"] = process
                state["video_path"] = str(video_path)
                state["backend"] = "macos-screencapture-started-unverified"
                time.sleep(1)
                if process.poll() is not None:
                    state["backend_error"] = stderr_path.read_text(encoding="utf-8", errors="replace")[:2000]
                    state["video_path"] = None
                    state["backend"] = "event-replay-video-start-failed"
            except OSError as error:
                state["backend_error"] = str(error)
        self.sessions[recording_id] = state
        self._write_event(state, "recording.started", {"backend": state["backend"]})
        self._write_manifest(state)
        return self.public_state(state)

    def capture_step(
        self,
        recording_id: str,
        action: str,
        details: dict[str, Any] | None = None,
        screenshot_path: str | None = None,
    ) -> dict[str, Any]:
        state = self._get(recording_id)
        if state["status"] != "recording":
            raise ValueError("recording is not active")
        frame = {
            "index": len(state["frames"]) + 1,
            "at": _iso_now(),
            "action": action,
            "details": details or {},
            "screenshot_path": screenshot_path,
        }
        state["frames"].append(frame)
        self._write_event(state, "browser.step", frame)
        self._write_manifest(state)
        return frame

    def stop(self, recording_id: str, status: str = "completed") -> dict[str, Any]:
        state = self._get(recording_id)
        process = state.get("process")
        if process is not None and process.poll() is None:
            try:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
        state["process"] = None
        state["status"] = status
        state["stopped_at"] = _iso_now()
        if state.get("video_path") and (
            not Path(state["video_path"]).exists() or Path(state["video_path"]).stat().st_size < 1024
        ):
            state["video_path"] = None
            state["backend"] = "event-replay-video-unavailable"
        elif state.get("video_path"):
            state["backend"] = "macos-video-file-created-needs-playback-check"
        self._write_event(state, "recording.stopped", {"status": status})
        self._write_replay_html(state)
        self._write_manifest(state)
        return self.public_state(state)

    def list(self) -> list[dict[str, Any]]:
        return [self.public_state(item) for item in self.sessions.values()]

    def get(self, recording_id: str) -> dict[str, Any] | None:
        state = self.sessions.get(recording_id)
        return self.public_state(state) if state else None

    @staticmethod
    def public_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
        if state is None:
            return None
        return {key: value for key, value in state.items() if key != "process"}

    def _get(self, recording_id: str) -> dict[str, Any]:
        state = self.sessions.get(recording_id)
        if state is None:
            raise KeyError(f"recording not found: {recording_id}")
        return state

    def _write_event(self, state: dict[str, Any], name: str, payload: dict[str, Any]) -> None:
        event = {"event": name, "recording_id": state["id"], "at": _iso_now(), **payload}
        with Path(state["events_path"]).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _write_manifest(self, state: dict[str, Any]) -> None:
        Path(state["manifest_path"]).write_text(
            json.dumps(self.public_state(state), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_replay_html(self, state: dict[str, Any]) -> None:
        rows = []
        for frame in state["frames"]:
            rows.append(
                "<tr>"
                f"<td>{frame['index']}</td><td>{frame['at']}</td>"
                f"<td>{escape(frame['action'])}</td><td><code>{escape(json.dumps(frame['details'], ensure_ascii=False))}</code></td>"
                f"<td>{escape(frame.get('screenshot_path') or '')}</td>"
                "</tr>"
            )
        html = f"""<!doctype html>
<meta charset="utf-8">
<title>{state['title']}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:32px;color:#202124}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d9dde3;padding:8px;text-align:left;vertical-align:top}}
th{{background:#eef2f7}}code{{white-space:pre-wrap}}
</style>
<h1>{state['title']}</h1>
<p>状态：{state['status']}　后端：{state['backend']}</p>
<p>视频：{state.get('video_path') or '当前环境未生成原生视频，以下为可审计操作回放'}</p>
<table><thead><tr><th>#</th><th>时间</th><th>操作</th><th>详情</th><th>截图</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
"""
        Path(state["replay_path"]).write_text(html, encoding="utf-8")


recordings = RecordingManager()
