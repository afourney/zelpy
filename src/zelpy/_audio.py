"""In-memory Ogg Opus parsing and muxing."""

from __future__ import annotations

import os
import struct
from io import BytesIO
from typing import BinaryIO


def read_ogg_opus(data: bytes) -> tuple[int, int, int, list[bytes]]:
    """Return sample rate, frames/packet, duration, and packets from Ogg Opus."""
    source = BytesIO(data)
    packets: list[bytes] = []
    partial = bytearray()
    while header := source.read(27):
        if len(header) != 27 or header[:4] != b"OggS":
            raise ValueError("Audio is not a valid Ogg stream")
        laces = source.read(header[26])
        if len(laces) != header[26]:
            raise ValueError("Audio contains a truncated Ogg segment table")
        body = source.read(sum(laces))
        if len(body) != sum(laces):
            raise ValueError("Audio contains a truncated Ogg page")
        offset = 0
        for size in laces:
            partial += body[offset : offset + size]
            offset += size
            if size < 255:
                packets.append(bytes(partial))
                partial.clear()
    if partial:
        raise ValueError("Audio contains a truncated Ogg packet")
    if len(packets) < 3 or not packets[0].startswith(b"OpusHead"):
        raise ValueError("Ogg stream does not contain Opus audio")
    sample_rate = struct.unpack_from("<I", packets[0], 12)[0] or 48_000
    audio_packets = packets[2:]
    if not audio_packets:
        raise ValueError("Ogg Opus stream contains no audio")
    frames, duration = opus_packet_shape(audio_packets[0])
    return sample_rate, frames, duration, audio_packets


def opus_packet_shape(packet: bytes) -> tuple[int, int]:
    """Return the frame count and total duration of an Opus packet."""
    if not packet:
        raise ValueError("Opus packet is empty")
    toc, code = packet[0], packet[0] & 3
    if code == 3 and len(packet) < 2:
        raise ValueError("Opus packet is truncated")
    frames = 1 if code == 0 else 2 if code in (1, 2) else packet[1] & 0x3F
    config = (toc >> 3) & 0x1F
    if config < 12:
        duration = (10, 20, 40, 60)[config & 3]
    elif config < 16:
        duration = (10, 20)[config & 1]
    else:
        duration = (2.5, 5, 10, 20)[config & 3]
    total = int(duration * frames)
    if total not in (5, 10, 20, 40, 60):
        raise ValueError(f"Unsupported Opus packet duration: {total} ms")
    return frames, total


class OggOpusWriter:
    """Tiny Ogg muxer for raw Opus packets."""

    def __init__(self, output: BinaryIO, sample_rate: int, packet_ms: int):
        self.output = output
        self.packet_ms = packet_ms
        self.serial = os.getpid() & 0xFFFFFFFF
        self.sequence = 0
        self.granule = 0
        self._pending_packet: bytes | None = None
        self._finalized = False
        head = b"OpusHead" + struct.pack("<BBHIhB", 1, 1, 0, sample_rate, 0, 0)
        self._page(head, 2, 0)
        self._page(
            b"OpusTags" + struct.pack("<I", 5) + b"zelpy" + struct.pack("<I", 0),
            0,
            0,
        )

    def _page(self, packet: bytes, flags: int, granule: int) -> None:
        if len(packet) >= 255 * 255:
            raise ValueError("Opus packet too large")
        laces = bytes([255]) * (len(packet) // 255) + bytes([len(packet) % 255])
        header = bytearray(
            b"OggS\0"
            + bytes([flags])
            + struct.pack("<QII", granule, self.serial, self.sequence)
            + b"\0\0\0\0"
            + bytes([len(laces)])
        )
        checksum = ogg_crc(header + laces + packet)
        struct.pack_into("<I", header, 22, checksum)
        self.output.write(header + laces + packet)
        self.sequence += 1

    def _write_audio_page(self, packet: bytes, flags: int = 0) -> None:
        self.granule += 48_000 * self.packet_ms // 1000
        self._page(packet, flags, self.granule)

    def write(self, packet: bytes) -> None:
        if self._finalized:
            raise RuntimeError("Ogg Opus stream is already finalized")
        if self._pending_packet is not None:
            self._write_audio_page(self._pending_packet)
        self._pending_packet = packet

    def finalize(self) -> None:
        """Write the final audio packet with the Ogg end-of-stream flag."""
        if self._finalized:
            return
        if self._pending_packet is None:
            raise ValueError("Cannot finalize an Ogg Opus stream without audio")
        self._write_audio_page(self._pending_packet, flags=4)
        self._pending_packet = None
        self._finalized = True


def ogg_crc(data: bytes) -> int:
    """Return the non-reflected CRC-32 used by Ogg."""
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = (
                ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
                if crc & 0x80000000
                else (crc << 1) & 0xFFFFFFFF
            )
    return crc
