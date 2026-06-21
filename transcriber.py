"""OpenAI audio transcription over a hand-rolled multipart POST.

Uses only the stdlib (http.client + ssl) so the backend needs zero pip
installs under decky-loader's bundled Python.

Endpoint: POST https://api.openai.com/v1/audio/transcriptions
Models:
  - gpt-4o-mini-transcribe : cheap, fast, accurate (default for short PTT clips)
  - gpt-4o-transcribe      : highest accuracy
  - whisper-1              : legacy; supports verbose_json / srt / vtt
All three accept response_format=json, which is all we need.
"""

import http.client
import json
import os
import ssl
import uuid

OPENAI_HOST = "api.openai.com"
OPENAI_PATH = "/v1/audio/transcriptions"

MODELS = ["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"]


def _build_multipart(fields, file_field, filename, file_bytes):
    boundary = "----WhisPTT" + uuid.uuid4().hex
    bb = boundary.encode("ascii")
    crlf = b"\r\n"
    parts = []
    for name, value in fields.items():
        parts.append(b"--" + bb + crlf)
        parts.append(
            ('Content-Disposition: form-data; name="%s"' % name).encode("utf-8")
            + crlf + crlf
        )
        parts.append(str(value).encode("utf-8") + crlf)
    parts.append(b"--" + bb + crlf)
    parts.append(
        ('Content-Disposition: form-data; name="%s"; filename="%s"'
         % (file_field, filename)).encode("utf-8") + crlf
    )
    parts.append(b"Content-Type: audio/wav" + crlf + crlf)
    parts.append(file_bytes + crlf)
    parts.append(b"--" + bb + b"--" + crlf)
    return boundary, b"".join(parts)


def transcribe(audio_path, api_key, model="gpt-4o-mini-transcribe",
               language=None, prompt=None, timeout=60):
    with open(audio_path, "rb") as f:
        audio = f.read()

    fields = {"model": model, "response_format": "json"}
    if language:
        fields["language"] = language      # ISO-639-1, e.g. "en"
    if prompt:
        fields["prompt"] = prompt           # bias spelling / vocabulary

    boundary, body = _build_multipart(
        fields, "file", os.path.basename(audio_path), audio
    )
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "Content-Length": str(len(body)),
    }

    conn = http.client.HTTPSConnection(
        OPENAI_HOST, timeout=timeout, context=ssl.create_default_context()
    )
    try:
        conn.request("POST", OPENAI_PATH, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        status = resp.status
    finally:
        conn.close()

    if status != 200:
        snippet = raw.decode("utf-8", "replace")[:500]
        raise RuntimeError("OpenAI API error %d: %s" % (status, snippet))

    return json.loads(raw.decode("utf-8")).get("text", "")
