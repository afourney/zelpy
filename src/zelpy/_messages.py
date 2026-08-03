"""Messages received from a Zello channel."""

from dataclasses import dataclass
from typing import Literal, TypeAlias


@dataclass(frozen=True, slots=True)
class TextMessage:
    """A text message received from a channel."""

    channel: str
    sender: str
    text: str

    @property
    def type(self) -> Literal["text"]:
        return "text"


@dataclass(frozen=True, slots=True)
class VoiceMessage:
    """A completed voice message containing Ogg Opus audio."""

    channel: str
    sender: str
    audio: bytes

    content_type: Literal["audio/ogg; codecs=opus"] = "audio/ogg; codecs=opus"

    @property
    def type(self) -> Literal["voice"]:
        return "voice"


ZelloMessage: TypeAlias = TextMessage | VoiceMessage
