from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.skills.skill_manager import SkillManager
from astrbot.core.tools.skill_tool import SkillTool


class _FakeSandboxFs:
    def __init__(
        self, files: dict[str, str], dirs: dict[str, list[dict]], fail_list=False
    ):
        self.files = files
        self.dirs = dirs
        self.fail_list = fail_list

    async def read_file(self, path: str, encoding: str = "utf-8") -> dict:
        _ = encoding
        if path not in self.files:
            return {"success": False, "error": "missing"}
        return {"success": True, "content": self.files[path]}

    async def list_dir(self, path: str, show_hidden: bool = False) -> dict:
        _ = show_hidden
        if self.fail_list:
            raise RuntimeError("list failed")
        return {"success": True, "entries": self.dirs.get(path, [])}


class _FakeSandboxBooter:
    def __init__(self, fs: _FakeSandboxFs):
        self.fs = fs


async def _fake_get_booter(context, umo, booter):
    _ = context, umo
    return booter


def _setup_skill_paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    data_dir = tmp_path / "data"
    temp_dir = tmp_path / "temp"
    skills_root = tmp_path / "skills"
    data_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    skills_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "astrbot.core.skills.skill_manager.get_astrbot_data_path",
        lambda: str(data_dir),
    )
    monkeypatch.setattr(
        "astrbot.core.skills.skill_manager.get_astrbot_temp_path",
        lambda: str(temp_dir),
    )
    return data_dir, temp_dir, skills_root


def _context() -> ContextWrapper:
    event = SimpleNamespace(unified_msg_origin="test_umo")
    agent_context = SimpleNamespace(context=None, event=event)
    return ContextWrapper(context=agent_context)


@pytest.mark.asyncio
async def test_skill_tool_loads_local_skill_content_and_files(tmp_path, monkeypatch):
    _, _, skills_root = _setup_skill_paths(monkeypatch, tmp_path)
    skill_dir = skills_root / "local-skill"
    skill_dir.joinpath("scripts").mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text("# Local Skill\n", encoding="utf-8")
    skill_dir.joinpath("scripts", "run.py").write_text(
        "print('ok')",
        encoding="utf-8",
    )
    manager = SkillManager(skills_root=str(skills_root))
    skill = manager.get_skill("local-skill", runtime="local")
    assert skill is not None
    tool = SkillTool(
        allowed_skills={skill.name: skill},
        skill_manager=manager,
        runtime="local",
    )

    result = await tool.call(_context(), name="local-skill")

    assert (
        f'<skill_content name="local-skill" runtime="local" directory="{skill_dir}">'
        in result
    )
    assert "# Local Skill" in result
    assert (
        f'<file path="{skill_dir / "scripts" / "run.py"}" relative="scripts/run.py" />'
        in result
    )


@pytest.mark.asyncio
async def test_skill_tool_rejects_unavailable_skill(tmp_path, monkeypatch):
    _, _, skills_root = _setup_skill_paths(monkeypatch, tmp_path)
    skill_dir = skills_root / "local-skill"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text("# Local Skill\n", encoding="utf-8")
    manager = SkillManager(skills_root=str(skills_root))
    skill = manager.get_skill("local-skill", runtime="local")
    assert skill is not None
    tool = SkillTool(
        allowed_skills={skill.name: skill},
        skill_manager=manager,
        runtime="local",
    )

    result = await tool.call(_context(), name="missing")

    assert result == (
        "error: Skill 'missing' is not available. Available skills: local-skill"
    )


@pytest.mark.asyncio
async def test_skill_tool_loads_sandbox_skill_content_and_files(tmp_path, monkeypatch):
    _, _, skills_root = _setup_skill_paths(monkeypatch, tmp_path)
    manager = SkillManager(skills_root=str(skills_root))
    manager.set_sandbox_skills_cache(
        [
            {
                "name": "sandbox-skill",
                "description": "sandbox skill",
                "path": "skills/sandbox-skill/SKILL.md",
            }
        ]
    )
    skill = manager.get_skill("sandbox-skill", runtime="sandbox")
    assert skill is not None
    fake_fs = _FakeSandboxFs(
        files={"skills/sandbox-skill/SKILL.md": "# Sandbox Skill\n"},
        dirs={
            "skills/sandbox-skill": [
                {"name": "SKILL.md", "type": "file"},
                {"name": "scripts", "type": "directory"},
            ],
            "skills/sandbox-skill/scripts": [
                {"name": "run.py", "type": "file"},
            ],
        },
    )
    monkeypatch.setattr(
        "astrbot.core.tools.skill_tool.get_booter",
        lambda context, umo: _fake_get_booter(
            context,
            umo,
            _FakeSandboxBooter(fake_fs),
        ),
    )
    tool = SkillTool(
        allowed_skills={skill.name: skill},
        skill_manager=manager,
        runtime="sandbox",
    )

    result = await tool.call(_context(), name="sandbox-skill")

    assert (
        '<skill_content name="sandbox-skill" runtime="sandbox" '
        'directory="skills/sandbox-skill">'
    ) in result
    assert "# Sandbox Skill" in result
    assert (
        '<file path="skills/sandbox-skill/scripts/run.py" relative="scripts/run.py" />'
        in result
    )


@pytest.mark.asyncio
async def test_skill_tool_returns_content_when_sandbox_file_listing_fails(
    tmp_path,
    monkeypatch,
):
    _, _, skills_root = _setup_skill_paths(monkeypatch, tmp_path)
    manager = SkillManager(skills_root=str(skills_root))
    manager.set_sandbox_skills_cache(
        [
            {
                "name": "sandbox-skill",
                "description": "sandbox skill",
                "path": "skills/sandbox-skill/SKILL.md",
            }
        ]
    )
    skill = manager.get_skill("sandbox-skill", runtime="sandbox")
    assert skill is not None
    fake_fs = _FakeSandboxFs(
        files={"skills/sandbox-skill/SKILL.md": "# Sandbox Skill\n"},
        dirs={},
        fail_list=True,
    )
    monkeypatch.setattr(
        "astrbot.core.tools.skill_tool.get_booter",
        lambda context, umo: _fake_get_booter(
            context,
            umo,
            _FakeSandboxBooter(fake_fs),
        ),
    )
    tool = SkillTool(
        allowed_skills={skill.name: skill},
        skill_manager=manager,
        runtime="sandbox",
    )

    result = await tool.call(_context(), name="sandbox-skill")

    assert "# Sandbox Skill" in result
    assert "warning: Unable to list auxiliary skill files: list failed" in result
    assert "<skill_files>" in result
