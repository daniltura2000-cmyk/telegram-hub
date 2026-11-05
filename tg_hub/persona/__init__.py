"""Персона: константи промптів та аналіз стилю."""
from .styles import DEFAULT_BASE_PROMPT, DEFAULT_STYLE_META_PROMPT
from .analyzer import (
    collect_outgoing_messages,
    build_style_user_prompt,
    generate_style_profile,
    DEFAULT_SAMPLE_CHAR_LIMIT,
)

__all__ = [
    "DEFAULT_BASE_PROMPT",
    "DEFAULT_STYLE_META_PROMPT",
    "collect_outgoing_messages",
    "build_style_user_prompt",
    "generate_style_profile",
    "DEFAULT_SAMPLE_CHAR_LIMIT",
]
