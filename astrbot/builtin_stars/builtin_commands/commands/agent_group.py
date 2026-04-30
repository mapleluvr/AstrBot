from __future__ import annotations

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult


class AgentGroupCommands:
    def __init__(self, context: star.Context) -> None:
        self.context = context

    async def agent_group(
        self,
        event: AstrMessageEvent,
        action: str | None = None,
        preset_name: str | None = None,
        *task_parts: str,
    ) -> None:
        manager = getattr(self.context, "agent_group_runtime_manager", None)
        if manager is None:
            self._reply(event, "Agent group runtime manager is not available.")
            return

        action = (action or "help").lower()
        if action == "list":
            presets = manager.list_presets()
            if not presets:
                self._reply(event, "No agent group presets configured.")
                return
            preset_names = ", ".join(preset.name for preset in presets)
            self._reply(event, f"Agent group presets: {preset_names}")
            return

        if action == "start":
            if not preset_name or not task_parts:
                self._reply(
                    event,
                    "Usage: /agent_group start <preset_name> <task>",
                )
                return
            result = await manager.start_run(
                event,
                preset_name,
                " ".join(str(part) for part in task_parts),
                runtime_context=self.context,
            )
            self._reply_with_result(
                event,
                result,
                success=lambda data: (
                    f"Agent group started: {data.get('run_id')} ({data.get('status')})."
                ),
            )
            return

        if action == "status":
            run_id = preset_name
            if not run_id:
                self._reply(event, "Usage: /agent_group status <run_id>")
                return
            result = await manager.get_status(run_id=run_id)
            self._reply_with_result(
                event,
                result,
                success=lambda data: (
                    f"Agent group {data.get('run_id')} status: {data.get('status')}."
                ),
            )
            return

        if action == "cancel":
            run_id = preset_name
            if not run_id:
                self._reply(event, "Usage: /agent_group cancel <run_id>")
                return
            result = await manager.cancel_run(run_id, runtime_context=self.context)
            self._reply_with_result(
                event,
                result,
                success=lambda data: f"Agent group {data.get('run_id')} cancelled.",
            )
            return

        self._reply(
            event,
            "Usage: /agent_group list | start <preset_name> <task> | "
            "status <run_id> | cancel <run_id>",
        )

    @staticmethod
    def _reply(event: AstrMessageEvent, message: str) -> None:
        event.set_result(MessageEventResult().message(message))

    def _reply_with_result(self, event, result, *, success) -> None:
        if result.ok:
            self._reply(event, success(result.data or {}))
            return
        error = result.error
        if error is None:
            self._reply(event, "Agent group operation failed.")
            return
        self._reply(event, f"{error.error_code}: {error.message}")
