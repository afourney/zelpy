"""Async client for Zello Channel API text and Opus voice messages."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import struct
from collections.abc import AsyncIterator
from typing import Any, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from ._audio import OggOpusWriter, read_ogg_opus
from ._credentials import ZelloCredentials
from ._messages import TextMessage, VoiceMessage, ZelloMessage

DEFAULT_ENDPOINT = "wss://zello.io/ws"
_MESSAGES_CLOSED = object()


class Zello:
    """An asynchronous client connected to one Zello channel."""

    def __init__(
        self,
        credentials: ZelloCredentials,
        channel: str,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
    ) -> None:
        self.credentials = credentials
        self.channel = channel
        self.endpoint = endpoint
        self.ws: Any = None
        self.receiver: asyncio.Task[None] | None = None
        self.seq = 0
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.online = asyncio.Event()
        self.incoming: dict[int, tuple[io.BytesIO, OggOpusWriter, str, str]] = {}
        self._messages: asyncio.Queue[ZelloMessage | object] = asyncio.Queue()
        self._messages_consumer_active = False

    async def __aenter__(self) -> Zello:
        self.ws = await connect(self.endpoint, ping_interval=None)
        self.receiver = asyncio.create_task(self._receive())
        response = await self.command(
            "logon",
            auth_token=self.credentials.auth_token(),
            username=self.credentials.username,
            password=self.credentials.password,
            channels=[self.channel],
        )
        if not response.get("success"):
            raise RuntimeError(response.get("error", "logon failed"))
        await asyncio.wait_for(self.online.wait(), 10)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.ws is not None:
            await self.ws.close()
        try:
            if self.receiver is not None:
                with contextlib.suppress(ConnectionClosed):
                    await self.receiver
        finally:
            for output, *_ in self.incoming.values():
                output.close()

    async def command(self, command: str, **fields: object) -> dict[str, Any]:
        self.seq += 1
        seq = self.seq
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[seq] = future
        try:
            await self.ws.send(json.dumps({"command": command, "seq": seq, **fields}))
            return await asyncio.wait_for(asyncio.shield(future), 10)
        finally:
            if self.pending.pop(seq, None) is not None and not future.done():
                future.cancel()

    async def _receive(self) -> None:
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    if len(message) >= 9 and message[0] == 1:
                        stream_id = struct.unpack_from(">I", message, 1)[0]
                        if stream_id in self.incoming:
                            self.incoming[stream_id][1].write(message[9:])
                    continue
                event = json.loads(message)
                seq = event.get("seq")
                if seq in self.pending:
                    self.pending.pop(seq).set_result(event)
                    continue
                command = event.get("command")
                if command == "on_channel_status" and event.get("status") == "online":
                    self.online.set()
                elif command == "on_text_message":
                    await self._messages.put(
                        TextMessage(
                            channel=str(event.get("channel", self.channel)),
                            sender=str(event.get("from", "unknown")),
                            text=str(event.get("text", "")),
                        )
                    )
                elif command == "on_stream_start" and event.get("codec") == "opus":
                    raw = base64.b64decode(event["codec_header"])
                    rate, _, packet_ms = struct.unpack("<HBB", raw)
                    output = io.BytesIO()
                    self.incoming[event["stream_id"]] = (
                        output,
                        OggOpusWriter(output, rate, packet_ms),
                        str(event.get("channel", self.channel)),
                        str(event.get("from", "unknown")),
                    )
                elif command == "on_stream_stop" and event.get("stream_id") in self.incoming:
                    output, writer, channel, sender = self.incoming.pop(event["stream_id"])
                    writer.finalize()
                    await self._messages.put(
                        VoiceMessage(
                            channel=channel,
                            sender=sender,
                            audio=output.getvalue(),
                        )
                    )
                    output.close()
        finally:
            await self._messages.put(_MESSAGES_CLOSED)

    async def messages(self) -> AsyncIterator[ZelloMessage]:
        """Iterate over text and completed voice messages from the channel.

        Only one active consumer is supported per client. Messages received
        before iteration begins are retained for that consumer.
        """
        if self._messages_consumer_active:
            raise RuntimeError("Zello.messages() already has an active consumer")
        self._messages_consumer_active = True
        try:
            while True:
                message = await self._messages.get()
                if message is _MESSAGES_CLOSED:
                    return
                yield cast(ZelloMessage, message)
        finally:
            self._messages_consumer_active = False

    async def send_text(self, text: str) -> None:
        response = await self.command("send_text_message", channel=self.channel, text=text)
        if not response.get("success"):
            raise RuntimeError(response.get("error", "text send failed"))

    async def send_voice(self, audio: bytes) -> None:
        """Send a complete Ogg Opus payload."""
        rate, frames, packet_ms, packets = read_ogg_opus(audio)
        codec_header = base64.b64encode(struct.pack("<HBB", rate, frames, packet_ms)).decode()
        response = await self.command(
            "start_stream",
            channel=self.channel,
            type="audio",
            codec="opus",
            codec_header=codec_header,
            packet_duration=packet_ms,
        )
        if not response.get("success"):
            raise RuntimeError(response.get("error", "voice stream failed"))
        stream_id = response["stream_id"]
        started = asyncio.get_running_loop().time()
        for index, packet in enumerate(packets):
            await self.ws.send(struct.pack(">BII", 1, stream_id, 0) + packet)
            deadline = started + (index + 1) * packet_ms / 1000
            await asyncio.sleep(max(0, deadline - asyncio.get_running_loop().time()))
        response = await self.command("stop_stream", channel=self.channel, stream_id=stream_id)
        if not response.get("success"):
            raise RuntimeError(response.get("error", "voice stream failed to stop"))
