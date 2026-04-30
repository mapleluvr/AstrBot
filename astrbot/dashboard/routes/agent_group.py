import copy
import inspect
import traceback
from typing import Any

from quart import jsonify, request

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle

from .route import Response, Route, RouteContext


def _normalize_agent_group_config(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {"presets": []}

    config = copy.deepcopy(raw)
    presets = config.get("presets")
    if not isinstance(presets, list):
        presets = []
    config["presets"] = presets
    config.setdefault("summary_preset", "agent_group_summary")

    for preset in presets:
        if not isinstance(preset, dict):
            continue
        preset.setdefault("enabled", True)
        preset.setdefault("members", [])
        preset.setdefault("initial_recipients", [])
        preset.setdefault("principles", [])
        preset.setdefault("collaboration_prompt", "")
        preset.setdefault("summary_preset", config["summary_preset"])
        preset.setdefault("summary_include_private", False)
        preset.setdefault("token_limit", None)
        preset.setdefault("time_limit_seconds", None)
        preset.pop("workspace_id", None)

        members = preset.get("members")
        if not isinstance(members, list):
            members = []
        normalized_members = []
        for member in members:
            if not isinstance(member, dict):
                continue
            member_name = str(member.get("name") or "").strip()
            if not member_name:
                continue
            source_type = str(member.get("source_type") or "").strip()
            subagent_preset = str(member.get("subagent_preset") or "").strip()
            persona_id = str(member.get("persona_id") or "").strip()
            if source_type not in {"subagent", "persona"}:
                source_type = (
                    "persona" if persona_id and not subagent_preset else "subagent"
                )
            if source_type == "subagent" and not subagent_preset:
                continue
            if source_type == "persona" and not persona_id:
                continue
            normalized_members.append(
                {
                    "name": member_name,
                    "source_type": source_type,
                    "subagent_preset": subagent_preset
                    if source_type == "subagent"
                    else "",
                    "persona_id": persona_id if source_type == "persona" else "",
                    "enabled": member.get("enabled", True) is not False,
                }
            )
        preset["members"] = normalized_members

    return config


class AgentGroupRoute(Route):
    def __init__(
        self,
        context: RouteContext,
        core_lifecycle: AstrBotCoreLifecycle,
    ) -> None:
        super().__init__(context)
        self.core_lifecycle = core_lifecycle
        self.routes = [
            ("/agent-group/config", ("GET", self.get_agent_group_config)),
            ("/agent-group/config", ("POST", self.update_agent_group_config)),
        ]
        self.register_routes()

    async def get_agent_group_config(self):
        try:
            cfg = self.core_lifecycle.astrbot_config
            data = _normalize_agent_group_config(cfg.get("agent_group"))
            return jsonify(Response().ok(data=data).__dict__)
        except Exception as e:
            logger.error(traceback.format_exc())
            return jsonify(
                Response().error(f"Failed to get agent group config: {e!s}").__dict__
            )

    async def update_agent_group_config(self):
        try:
            data = await request.json
            if not isinstance(data, dict):
                return jsonify(
                    Response().error("Config must be a JSON object").__dict__
                )

            cfg = self.core_lifecycle.astrbot_config
            cfg["agent_group"] = _normalize_agent_group_config(data)
            cfg.save_config()

            runtime_manager = getattr(
                self.core_lifecycle,
                "agent_group_runtime_manager",
                None,
            )
            if runtime_manager is not None:
                result = runtime_manager.reload_from_config(cfg["agent_group"])
                if inspect.isawaitable(result):
                    await result

            return jsonify(Response().ok(message="Saved successfully").__dict__)
        except Exception as e:
            logger.error(traceback.format_exc())
            return jsonify(
                Response().error(f"Failed to save agent group config: {e!s}").__dict__
            )
