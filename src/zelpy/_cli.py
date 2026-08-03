"""Testing CLI for sending and receiving messages with Zello."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from ._audio import read_ogg_opus
from ._credentials import ZelloCredentials
from ._messages import TextMessage, VoiceMessage
from ._zello import DEFAULT_ENDPOINT, Zello

DEFAULT_CREDENTIALS = Path("zello.yaml")


def load_yaml_credentials(path: Path) -> ZelloCredentials:
    """Load Zello credentials from the YAML format supported by the CLI."""
    path = path.expanduser()
    data: Any = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    try:
        return ZelloCredentials.from_mapping(data)
    except ValueError as error:
        raise ValueError(f"Invalid credentials in {path}: {error}") from error


def load_voice(path: Path) -> bytes:
    """Read Ogg Opus audio, converting other formats with ffmpeg when needed."""
    audio = path.read_bytes()
    try:
        read_ogg_opus(audio)
        return audio
    except ValueError:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                f"{path} is not Ogg Opus audio and ffmpeg is not installed"
            ) from None
        result = subprocess.run(
            [
                ffmpeg,
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "libopus",
                "-frame_duration",
                "20",
                "-f",
                "opus",
                "pipe:1",
            ],
            check=True,
            stdout=subprocess.PIPE,
        )
        read_ogg_opus(result.stdout)
        return result.stdout


def save_voice(message: VoiceMessage, output_directory: Path) -> Path:
    """Save a finalized voice message and return its path."""
    output_directory.mkdir(parents=True, exist_ok=True)
    sender = re.sub(r"[^A-Za-z0-9_.-]", "_", message.sender)
    path = output_directory / f"{time.time_ns()}-{sender}.opus"
    path.write_bytes(message.audio)
    return path


async def run(args: argparse.Namespace) -> None:
    credentials = load_yaml_credentials(args.credentials)
    async with Zello(
        credentials,
        args.channel,
        endpoint=args.endpoint,
    ) as zello:
        if args.action == "send-text":
            await zello.send_text(args.text)
            print("Text sent")
        elif args.action == "send-voice":
            await zello.send_voice(load_voice(args.file))
            print("Voice sent")
        else:
            print(f"Listening on {args.channel}; press Ctrl-C to stop")
            async for message in zello.messages():
                if isinstance(message, TextMessage):
                    print(f"TEXT {message.channel} <{message.sender}> {message.text}")
                elif isinstance(message, VoiceMessage):
                    path = save_voice(message, args.output)
                    print(f"VOICE {message.channel} <{message.sender}> -> {path}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--channel", required=True)
    root.add_argument("--credentials", "--keys", type=Path, default=DEFAULT_CREDENTIALS)
    root.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    root.add_argument("--output", type=Path, default=Path("received"))
    actions = root.add_subparsers(dest="action", required=True)
    text = actions.add_parser("send-text")
    text.add_argument("text")
    voice = actions.add_parser("send-voice")
    voice.add_argument("file", type=Path)
    actions.add_parser("receive")
    return root


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(parser().parse_args()))


if __name__ == "__main__":
    main()
