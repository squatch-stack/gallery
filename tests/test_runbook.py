"""Keep shell examples in the operator runbook aligned with tool CLIs."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs/runbook.md"
BLOCK = re.compile(r"```sh\s*\n(.*?)```", re.DOTALL)
TOOL = re.compile(r"^tools/[^\s]+\.(?:py|sh)$")


def shell_commands() -> list[list[str]]:
    commands = []
    for block in BLOCK.findall(RUNBOOK.read_text(encoding="utf-8")):
        logical = block.replace("\\\n", " ")
        for line in logical.splitlines():
            if line.strip():
                commands.append(shlex.split(line, comments=True))
    return commands


def mentioned_tools(command: list[str]) -> list[str]:
    return [token for token in command if TOOL.match(token)]


def python_tool(command: list[str]) -> str | None:
    return next((tool for tool in mentioned_tools(command) if tool.endswith(".py")), None)


def help_output(tool: str) -> str:
    result = subprocess.run(
        [sys.executable, tool, "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"{tool} --help failed: {output}"
    return output


def test_every_mentioned_tool_exists() -> None:
    tools = {tool for command in shell_commands() for tool in mentioned_tools(command)}
    assert tools
    assert not [tool for tool in sorted(tools) if not (ROOT / tool).is_file()]


@pytest.mark.parametrize(
    "command",
    [command for command in shell_commands() if python_tool(command)],
    ids=lambda command: " ".join(command),
)
def test_python_flags_appear_in_help(command: list[str]) -> None:
    tool = python_tool(command)
    assert tool is not None
    output = help_output(tool)
    flags = {token.split("=", 1)[0] for token in command if token.startswith("--")}
    missing = sorted(flag for flag in flags if flag not in output)
    assert not missing, f"{tool} --help does not contain: {', '.join(missing)}"
