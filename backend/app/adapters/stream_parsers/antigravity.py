"""Antigravity stream-json activity, without model reasoning or tool output."""
import json
from pathlib import PurePath
import re
import shlex
from typing import Optional

from backend.app.adapters.stream_events import StreamEvent, StreamEventType


def _summary(name: str, parameters: dict) -> str:
    labels = {
        "view_file": "读取文件", "sed_file": "读取文件",
        "list_dir": "查看目录", "find_by_name": "查找文件",
        "grep_search": "搜索代码", "write_to_file": "写入文件",
        "replace_file_content": "修改文件", "multi_replace_file_content": "修改文件",
        "command_status": "检查命令进度", "run_command": "执行命令",
        "read_url_content": "读取网页", "search_web": "搜索网页",
    }
    label = labels.get(name, "调用工具 · " + name)
    if name == "run_command":
        command = parameters.get("CommandLine")
        if isinstance(command, str):
            try:
                words = shlex.split(command)
            except ValueError:
                words = []
            if words and words[0] == "git":
                return "检查 Git 状态" if "status" in words else "执行 Git 命令"
            if any(word in {"pytest", "vitest", "test"} for word in words):
                return "运行测试"
        return label
    # Only a basename is exposed. Commands, search strings, tool output and
    # arbitrary model-provided summaries may contain credentials/project text.
    if name in {"view_file", "sed_file", "write_to_file", "replace_file_content", "multi_replace_file_content"}:
        path = parameters.get("AbsolutePath") or parameters.get("TargetFile")
        if isinstance(path, str):
            basename = PurePath(path).name
            basename = re.sub(r"[\x00-\x1f\x7f]", "", basename)[:100]
            if basename:
                return label + " · " + basename
    return label


class AntigravityStreamParser:
    def __init__(self):
        self._session = ""
        self._seen: set[tuple[int, str]] = set()

    def parse(self, line: str) -> Optional[StreamEvent]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None
        if event.get("event") == "init":
            session = event.get("conversation_id")
            if isinstance(session, str):
                self._session = session
                return StreamEvent(StreamEventType.SESSION_STARTED, session_id=session)
        if event.get("event") == "result":
            result = event.get("result")
            if isinstance(result, dict):
                return StreamEvent(
                    StreamEventType.FINISHED if result.get("status") == "SUCCESS" else StreamEventType.ERROR,
                )
        if event.get("event") != "step_update":
            return None
        step = event.get("step_update")
        if not isinstance(step, dict) or step.get("step_type") != "tool":
            return None
        index, state, name = step.get("step_index"), step.get("state"), step.get("tool_name")
        if not isinstance(index, int) or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name):
            return None
        if not isinstance(state, str) or state not in {"ACTIVE", "DONE", "ERROR", "FAILED", "CANCELLED"} or (index, state) in self._seen:
            return None
        self._seen.add((index, state))
        info = step.get("tool_info")
        parameters = info.get("parameters", {}) if isinstance(info, dict) else {}
        phase = "started" if state == "ACTIVE" else "completed" if state == "DONE" else "failed"
        return StreamEvent(
            StreamEventType.TOOL_ATTEMPT, tool_name=name,
            activity_id=f"{self._session}:{index}", tool_phase=phase,
            tool_summary=_summary(name, parameters if isinstance(parameters, dict) else {}),
        )
