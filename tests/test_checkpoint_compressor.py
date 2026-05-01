"""Tests for CheckpointCompressor."""

import pytest
from astrbot.core.agent.context.compressor import (
    CheckpointCompressor,
    split_history,
)
from astrbot.core.agent.message import Message


class TestCheckpointCompressor:
    def test_should_compress_below_threshold(self):
        from unittest.mock import MagicMock
        provider = MagicMock()
        compressor = CheckpointCompressor(provider=provider)
        assert compressor.should_compress([], 1000, 2000) is False  # 50%

    def test_should_compress_above_threshold(self):
        from unittest.mock import MagicMock
        provider = MagicMock()
        compressor = CheckpointCompressor(provider=provider)
        assert compressor.should_compress([], 1800, 2000) is True  # 90%

    def test_should_compress_zero_max(self):
        from unittest.mock import MagicMock
        provider = MagicMock()
        compressor = CheckpointCompressor(provider=provider)
        assert compressor.should_compress([], 1000, 0) is False

    def test_should_compress_zero_tokens(self):
        from unittest.mock import MagicMock
        provider = MagicMock()
        compressor = CheckpointCompressor(provider=provider)
        assert compressor.should_compress([], 0, 2000) is False


class TestSplitHistory:
    def test_split_history_normal(self):
        msgs = [
            Message(role="system", content="sys"),
            Message(role="user", content="u1"),
            Message(role="assistant", content="a1"),
            Message(role="user", content="u2"),
            Message(role="assistant", content="a2"),
            Message(role="user", content="u3"),
            Message(role="assistant", content="a3"),
        ]
        sys_msgs, to_summarize, recent = split_history(msgs, keep_recent=4)
        assert len(sys_msgs) == 1
        assert sys_msgs[0].role == "system"
        assert len(recent) >= 4

    def test_split_history_short(self):
        msgs = [
            Message(role="user", content="u1"),
            Message(role="assistant", content="a1"),
        ]
        sys_msgs, to_summarize, recent = split_history(msgs, keep_recent=10)
        assert len(to_summarize) == 0
        assert len(recent) == len(msgs)

    def test_split_history_no_system(self):
        msgs = [
            Message(role="user", content="u1"),
            Message(role="assistant", content="a1"),
            Message(role="user", content="u2"),
            Message(role="assistant", content="a2"),
            Message(role="user", content="u3"),
            Message(role="assistant", content="a3"),
        ]
        sys_msgs, to_summarize, recent = split_history(msgs, keep_recent=2)
        assert len(sys_msgs) == 0
        assert len(recent) == 2


class TestCheckpointSchema:
    def test_parse_checkpoint_yaml_valid(self):
        from astrbot.core.agent.checkpoint.schema import parse_checkpoint_yaml
        yaml_text = """\
checkpoint_version: 1
covers:
  start_turn: 1
  end_turn: 5
session_intent:
  primary_goal: "test"
  status: in_progress
"""
        result = parse_checkpoint_yaml(yaml_text)
        assert result is not None
        assert result["checkpoint_version"] == 1
        assert result["session_intent"]["status"] == "in_progress"

    def test_parse_checkpoint_yaml_with_fence(self):
        from astrbot.core.agent.checkpoint.schema import parse_checkpoint_yaml
        yaml_text = '```yaml\ncheckpoint_version: 1\n```'
        result = parse_checkpoint_yaml(yaml_text)
        assert result is not None
        assert result["checkpoint_version"] == 1

    def test_parse_checkpoint_yaml_invalid(self):
        from astrbot.core.agent.checkpoint.schema import parse_checkpoint_yaml
        result = parse_checkpoint_yaml("not: valid: yaml: [")
        assert result is None
