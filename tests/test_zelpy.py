import asyncio
import io
from pathlib import Path

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from zelpy import TextMessage, VoiceMessage, Zello, ZelloCredentials
from zelpy._audio import OggOpusWriter, ogg_crc, read_ogg_opus
from zelpy._cli import load_voice, load_yaml_credentials, parser, save_voice
from zelpy._zello import _MESSAGES_CLOSED


def credential_values() -> dict[str, str]:
    return {
        "developer_token": "token",
        "username": "alice",
        "password": "secret",
        "issuer": "issuer",
        "public_key": "public key",
        "private_key": "private key",
    }


def ogg_opus_bytes() -> bytes:
    packets = [bytes([0x98, i]) for i in range(3)]
    output = io.BytesIO()
    writer = OggOpusWriter(output, 16000, 20)
    for packet in packets:
        writer.write(packet)
    writer.finalize()
    return output.getvalue()


def test_ogg_writer_round_trip():
    packets = [bytes([0x98, i]) for i in range(3)]

    rate, frames, duration, actual = read_ogg_opus(ogg_opus_bytes())
    assert (rate, frames, duration, actual) == (16000, 1, 20, packets)


def test_ogg_crc_is_written():
    output = io.BytesIO()
    writer = OggOpusWriter(output, 16000, 20)
    writer.write(bytes([0x98, 0]))
    writer.finalize()
    assert output.getvalue()[22:26] != b"\0\0\0\0"


def test_ogg_writer_marks_last_page_end_of_stream():
    audio = ogg_opus_bytes()
    final_page = audio.rfind(b"OggS")
    assert audio[final_page + 5] & 4


def test_ogg_crc_known_value():
    assert ogg_crc(b"123456789") == 0x89A1897F


def test_credentials_from_mapping():
    credentials = ZelloCredentials.from_mapping(credential_values())
    assert credentials.username == "alice"
    assert credentials.password == "secret"


def test_credentials_require_every_field():
    with pytest.raises(
        ValueError,
        match=r"developer_token.*password.*issuer.*public_key.*private_key",
    ):
        ZelloCredentials.from_mapping({"username": "alice"})


def test_cli_loads_yaml_credentials(tmp_path: Path):
    path = tmp_path / "zello.yaml"
    path.write_text(
        "developer_token: token\n"
        "username: alice\n"
        "password: secret\n"
        "issuer: issuer\n"
        "public_key: |\n  public line 1\n  public line 2\n"
        "private_key: |\n  private line 1\n  private line 2\n"
    )
    credentials = load_yaml_credentials(path)
    assert credentials.public_key == "public line 1\npublic line 2\n"
    assert credentials.private_key == "private line 1\nprivate line 2\n"


def test_cli_reads_native_voice_without_conversion(tmp_path: Path):
    path = tmp_path / "message.opus"
    expected = ogg_opus_bytes()
    path.write_bytes(expected)
    assert load_voice(path) == expected


def test_cli_saves_finalized_voice(tmp_path: Path):
    audio = ogg_opus_bytes()
    message = VoiceMessage(channel="test", sender="alice/example", audio=audio)
    path = save_voice(message, tmp_path)
    assert path.parent == tmp_path
    assert path.name.endswith("-alice_example.opus")
    assert path.read_bytes() == audio


def test_cli_requires_channel(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as error:
        parser().parse_args(["receive"])

    assert error.value.code == 2
    assert "the following arguments are required: --channel" in capsys.readouterr().err


class FakeWebSocket:
    async def close(self):
        pass


def zello_with_receiver(receiver):
    zello = Zello.__new__(Zello)
    zello.ws = FakeWebSocket()
    zello.receiver = receiver
    zello.incoming = {}
    zello._messages = asyncio.Queue()
    zello._messages_consumer_active = False
    return zello


def test_messages_yields_queued_messages():
    async def scenario():
        zello = zello_with_receiver(None)
        text = TextMessage(channel="test", sender="alice", text="hello")
        voice = VoiceMessage(channel="test", sender="bob", audio=ogg_opus_bytes())
        await zello._messages.put(text)
        await zello._messages.put(voice)
        await zello._messages.put(_MESSAGES_CLOSED)

        assert [message async for message in zello.messages()] == [text, voice]

    asyncio.run(scenario())


def test_messages_rejects_concurrent_consumers():
    async def scenario():
        zello = zello_with_receiver(None)
        first = zello.messages()
        pending = asyncio.create_task(anext(first))
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="active consumer"):
            await anext(zello.messages())
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await first.aclose()

    asyncio.run(scenario())


def test_shutdown_ignores_missing_peer_close_frame():
    async def scenario():
        async def receiver():
            raise ConnectionClosedError(None, Close(1000, ""))

        zello = zello_with_receiver(asyncio.create_task(receiver()))
        await zello.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_shutdown_preserves_unexpected_receiver_errors():
    async def scenario():
        async def receiver():
            raise RuntimeError("receiver failed")

        zello = zello_with_receiver(asyncio.create_task(receiver()))
        with pytest.raises(RuntimeError, match="receiver failed"):
            await zello.__aexit__(None, None, None)

    asyncio.run(scenario())
