from __future__ import annotations

from dataclasses import dataclass as std_dataclass
from dataclasses import field
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.computer.computer_client import get_booter
from astrbot.core.skills.skill_manager import SkillInfo, SkillManager


@std_dataclass(frozen=True)
class SkillFileRef:
    path: str
    relative: str


@dataclass(config={"arbitrary_types_allowed": True})
class SkillTool(FunctionTool[AstrAgentContext]):
    name: str = "skill"
    description: str = (
        "Load a specialized skill by name. Use this before following a skill's "
        "instructions. Available skills are listed in the system prompt."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name of the skill to load.",
                },
            },
            "required": ["name"],
        },
    )
    allowed_skills: dict[str, SkillInfo] = field(default_factory=dict, repr=False)
    skill_manager: Any = field(default_factory=SkillManager, repr=False)
    runtime: str = "local"
    max_files: int = 20

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        name: str,
    ) -> ToolExecResult:
        skill_name = str(name or "").strip()
        if not skill_name:
            return "error: Skill name must be a non-empty string."

        skill = self.allowed_skills.get(skill_name)
        if skill is None:
            available = ", ".join(sorted(self.allowed_skills)) or "none"
            return (
                f"error: Skill '{skill_name}' is not available. "
                f"Available skills: {available}"
            )

        if self.runtime == "sandbox":
            return await self._load_sandbox_skill(context, skill_name, skill)

        content = self._read_local_skill(skill)
        if content is None:
            return (
                f"error: Skill '{skill_name}' is registered but SKILL.md is "
                f"missing: {skill.path}"
            )
        directory = str(Path(skill.path).parent)
        files = [
            SkillFileRef(path=str(Path(directory) / relative), relative=relative)
            for relative in self.skill_manager.list_local_skill_files(
                skill_name, self.max_files
            )
        ]
        return self._format_skill_content(skill_name, content, directory, files)

    async def _load_sandbox_skill(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str,
        skill: SkillInfo,
    ) -> str:
        try:
            booter = await get_booter(
                context.context.context,
                context.context.event.unified_msg_origin,
            )
        except Exception as exc:  # noqa: BLE001
            return (
                "error: Sandbox runtime is not ready; cannot load sandbox skill "
                f"'{skill_name}'. {exc}"
            )

        content = await self._read_sandbox_skill(booter, skill.path)
        if content is None:
            return (
                f"error: Skill '{skill_name}' is registered in sandbox but "
                f"SKILL.md could not be read: {skill.path}"
            )

        warning = None
        directory = PurePosixPath(skill.path.replace("\\", "/")).parent.as_posix()
        try:
            files = await self._list_sandbox_skill_files(booter, skill.path)
        except Exception as exc:  # noqa: BLE001
            warning = f"warning: Unable to list auxiliary skill files: {exc}"
            files = []
        return self._format_skill_content(
            skill_name,
            content,
            directory,
            files,
            warning=warning,
        )

    async def _read_sandbox_skill(self, booter, path: str) -> str | None:
        fs = getattr(booter, "fs", None)
        read_file = getattr(fs, "read_file", None)
        if read_file is None:
            return None
        result = await read_file(path, encoding="utf-8")
        if isinstance(result, dict):
            if result.get("success") is False:
                return None
            content = result.get("content")
            return content if isinstance(content, str) else None
        return result if isinstance(result, str) else None

    async def _list_sandbox_skill_files(
        self, booter, skill_path: str
    ) -> list[SkillFileRef]:
        fs = getattr(booter, "fs", None)
        list_dir = getattr(fs, "list_dir", None)
        if list_dir is None:
            return []

        skill_dir = PurePosixPath(skill_path.replace("\\", "/")).parent
        files: list[SkillFileRef] = []
        await self._collect_sandbox_files(list_dir, skill_dir, skill_dir, files)
        return files

    async def _collect_sandbox_files(
        self,
        list_dir,
        base_dir: PurePosixPath,
        current_dir: PurePosixPath,
        files: list[SkillFileRef],
    ) -> None:
        if len(files) >= self.max_files:
            return

        result = await list_dir(str(current_dir), show_hidden=False)
        entries = result.get("entries", []) if isinstance(result, dict) else []
        for entry in entries:
            name = self._sandbox_entry_name(entry)
            if not name:
                continue
            full_path = current_dir / name
            if self._sandbox_entry_is_dir(entry):
                await self._collect_sandbox_files(list_dir, base_dir, full_path, files)
                if len(files) >= self.max_files:
                    return
                continue
            if name == "SKILL.md":
                continue
            relative = full_path.relative_to(base_dir).as_posix()
            files.append(SkillFileRef(path=full_path.as_posix(), relative=relative))
            if len(files) >= self.max_files:
                return

    def _sandbox_entry_name(self, entry) -> str:
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("path")
            return str(name or "").strip()
        return ""

    def _sandbox_entry_is_dir(self, entry) -> bool:
        if not isinstance(entry, dict):
            return False
        if isinstance(entry.get("is_dir"), bool):
            return entry["is_dir"]
        if isinstance(entry.get("isDirectory"), bool):
            return entry["isDirectory"]
        return str(entry.get("type") or "").lower() in {"dir", "directory"}

    def _read_local_skill(self, skill: SkillInfo) -> str | None:
        if not skill.local_exists:
            return None
        skill_path = Path(skill.path)
        if not skill_path.is_file():
            return None
        return skill_path.read_text(encoding="utf-8")

    def _format_skill_content(
        self,
        skill_name: str,
        content: str,
        directory: str,
        files: list[SkillFileRef],
        warning: str | None = None,
    ) -> str:
        file_lines = [
            f'  <file path="{file.path}" relative="{file.relative}" />'
            for file in files
        ]
        files_block = "\n".join(file_lines)
        warning_block = f"\n{warning}\n" if warning else "\n"
        return (
            f'<skill_content name="{skill_name}" runtime="{self.runtime}" '
            f'directory="{directory}">\n'
            f"{content.rstrip()}\n"
            f"{warning_block}"
            "<skill_files>\n"
            f"{files_block}\n"
            "</skill_files>\n"
            "</skill_content>"
        )
