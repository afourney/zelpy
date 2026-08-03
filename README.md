# zelpy

`zelpy` is an async Python client for sending and receiving text and Ogg Opus
voice messages with the Zello Channel API. The library operates entirely on
in-memory data; the included CLI provides convenient file and audio conversion
support for testing.

## Library usage

```python
from zelpy import TextMessage, VoiceMessage, Zello, ZelloCredentials

credentials = ZelloCredentials(
    developer_token="...",
    username="...",
    password="...",
    issuer="...",
    public_key="...",
    private_key="...",
)

async with Zello(credentials, "my-channel") as client:
    await client.send_text("Hello from Python")

    async for message in client.messages():
        if isinstance(message, TextMessage):
            print(message.sender, message.text)
        elif isinstance(message, VoiceMessage):
            print(message.sender, message.audio)
```

Pass complete Ogg Opus bytes to `client.send_voice(audio)`. Received
`VoiceMessage.audio` values are also complete Ogg Opus byte strings.

## CLI usage

```bash
# Listen for text and voice messages
uv run zelpy --channel my-channel receive

# Send a text message
uv run zelpy --channel my-channel send-text "Hello from Python"

# Send an audio file
uv run zelpy --channel my-channel send-voice message.wav
```

The CLI accepts native Ogg Opus audio directly. For other audio formats, it
uses `ffmpeg` when available. Received voice messages are saved under
`./received/` by default.

## `zello.yaml`

The CLI reads credentials from `./zello.yaml` in the current working directory:

```yaml
developer_token: "..."
username: "..."
password: "..."
issuer: "..."
public_key: |
  -----BEGIN PUBLIC KEY-----
  ...
  -----END PUBLIC KEY-----
private_key: |
  -----BEGIN PRIVATE KEY-----
  ...
  -----END PRIVATE KEY-----
```

Use a different file with `--credentials /path/to/zello.yaml`.

## Obtaining credentials

See Zello's [Channel API authentication guide](https://github.com/zelloptt/zello-channel-api/blob/main/AUTH.md), then:

1. Go to the [Zello Developer Console](https://developers.zello.com/) and click **Login**.
2. Enter your Zello username and password. If you don't have an account, download the Zello app and create one.
3. Complete every field in the developer profile and click **Submit**.
4. Click **Keys**, then **Add Key**.
5. Copy and save the **Sample Development Token**, **Issuer**, and **Private Key**. Use **Select All** to ensure that each complete value is copied.
6. Click **Close**.

The developer credentials expire every 30 days. See the [Channel API authentication guide](https://github.com/zelloptt/zello-channel-api/blob/main/AUTH.md) for renewal instructions and more information.
