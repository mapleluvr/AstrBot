"""Checkpoint YAML schema, prompt templates, and output parser."""

import re
from typing import Any

CHECKPOINT_SYSTEM_PROMPT = """\
You are a state checkpoint recorder for a long-running task session.

Your job is NOT to solve the task or continue reasoning.
Your job is to carefully read the interaction transcript and previous checkpoint,
then produce an updated structured checkpoint that documents:

- current goal and status
- user constraints (hard and soft)
- user preferences
- important background / domain entities
- completed work
- decisions made (with reasons and status: active / superseded)
- observed results from tool calls
- failed attempts and why
- pending questions
- next actions
- uncertainties

You must distinguish:
- completed work vs planned work
- verified facts vs hypotheses
- user requirements vs assumptions

Do NOT invent facts.
Do NOT mark a plan as completed unless the transcript proves it.
Output ONLY the YAML checkpoint. Do not add any commentary.
"""  # noqa: E501

PREVIOUS_SUMMARY_INJECTION = """\
This is the previous state checkpoint.

<PREVIOUS_CHECKPOINT>
{previous_checkpoint}
</PREVIOUS_CHECKPOINT>
"""

FORCED_ACKNOWLEDGMENT = "OK. I have read the previous checkpoint. I am now ready to receive the new progress. I will update the checkpoint strictly based on the transcript and will not invent unsupported facts."

NEW_PROGRESS_HEADER = """\
The following is the recent interaction progress.

<PROGRESS_TRANSCRIPT start_message_id="{start_id}" end_message_id="{end_id}">
"""

NEW_PROGRESS_FOOTER = """
</PROGRESS_TRANSCRIPT>
"""

FINAL_PROMPT_TEMPLATE = """\
Now you have received all new progress.

Before generating, make sure you captured:
- changed goals or constraints
- decisions
- completed actions
- tool results
- files or code areas involved
- unresolved issues
- next actions
- uncertainties

Now output the updated checkpoint only.
The new checkpoint must cover turns {start_turn} through {end_turn}.
"""


def build_checkpoint_prompt(
    previous_checkpoint: str | None,
    version: int,
    start_turn: int,
    end_turn: int,
) -> tuple[str, list[dict[str, str]]]:
    """Build prompt messages for checkpoint generation.

    Returns (system_prompt, messages_list) where messages_list is
    the conversation to inject before the transcript.
    """
    messages: list[dict[str, str]] = []

    if previous_checkpoint:
        messages.append(
            {
                "role": "user",
                "content": PREVIOUS_SUMMARY_INJECTION.format(
                    previous_checkpoint=previous_checkpoint,
                ),
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": FORCED_ACKNOWLEDGMENT,
            }
        )

    return CHECKPOINT_SYSTEM_PROMPT, messages


def parse_checkpoint_yaml(text: str) -> dict[str, Any] | None:
    """Parse checkpoint YAML from LLM output.

    Handles outputs that may have markdown code fences.
    Returns parsed dict or None if parsing fails.
    """
    import yaml

    # Strip optional ```yaml fences
    cleaned = text.strip()
    fence_match = re.match(r"```(?:yaml)?\s*\n(.*?)\n```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        return yaml.safe_load(cleaned)
    except Exception:
        return None
