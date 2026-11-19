"""Agora RTC tokens (AccessToken2, "007") and ConvoAI agent join/leave."""
from __future__ import annotations

import base64
import hmac
import secrets
import struct
import time
import zlib
from collections import OrderedDict
from hashlib import sha256

import httpx

from . import config


# ---- AccessToken2, ported from AgoraIO/Tools DynamicKey/AccessToken2 (python3) ----

def _u16(x: int) -> bytes:
    return struct.pack("<H", int(x))


def _u32(x: int) -> bytes:
    return struct.pack("<I", int(x))


def _str(b: bytes) -> bytes:
    return _u16(len(b)) + b


def _map_u32(d: dict[int, int]) -> bytes:
    out = _u16(len(d))
    for k, v in OrderedDict(sorted(d.items())).items():
        out += _u16(k) + _u32(v)
    return out


def build_token(channel: str, uid: int | str, expire: int = 3600) -> str:
    """RTC token (join + publish audio/video/data) plus RTM login, like RtcTokenBuilder2.build_token_with_rtm."""
    if not config.AGORA_APP_CERT:
        return ""  # project in testing mode: no token needed
    app_id = config.AGORA_APP_ID.encode()
    cert = config.AGORA_APP_CERT.encode()
    issue_ts = int(time.time())
    salt = secrets.randbelow(99999998) + 1
    uid_s = "" if str(uid) == "0" else str(uid)

    rtc = _u16(1) + _map_u32({1: expire, 2: expire, 3: expire, 4: expire}) + _str(channel.encode()) + _str(uid_s.encode())
    rtm = _u16(2) + _map_u32({1: expire}) + _str(str(uid).encode())

    signing = hmac.new(_u32(issue_ts), cert, sha256).digest()
    signing = hmac.new(_u32(salt), signing, sha256).digest()
    info = _str(app_id) + _u32(issue_ts) + _u32(expire) + _u32(salt) + _u16(2) + rtc + rtm
    signature = hmac.new(signing, info, sha256).digest()
    return "007" + base64.b64encode(zlib.compress(_str(signature) + info)).decode()


# ---- ConvoAI REST ----

def _auth() -> tuple[str, str]:
    return (config.AGORA_CUSTOMER_ID, config.AGORA_CUSTOMER_SECRET)


def join_body(session_id: str, channel: str, user_uid: int, greeting: str, language: str) -> dict:
    kannada = language == "kn"
    asr_lang = "unknown" if kannada else config.ASR_LANGUAGE
    tts_params = {
        "api_key": config.MURF_API_KEY,
        "voiceId": (config.MURF_VOICE_ID_KN if kannada and config.MURF_VOICE_ID_KN else config.MURF_VOICE_ID),
        "locale": "kn-IN" if kannada and config.MURF_VOICE_ID_KN else config.MURF_LOCALE,
        "model": config.MURF_MODEL,
        "sample_rate": 24000,
    }
    if config.MURF_BASE_URL:
        tts_params["base_url"] = config.MURF_BASE_URL
    return {
        "name": f"bima-{session_id}",
        "properties": {
            "channel": channel,
            "token": build_token(channel, config.AGENT_RTC_UID),
            "agent_rtc_uid": str(config.AGENT_RTC_UID),
            "remote_rtc_uids": [str(user_uid)],
            "idle_timeout": 120,
            "asr": {"vendor": "sarvam", "language": "en-US",
                    "params": {"api_key": config.SARVAM_API_KEY, "language": asr_lang}},
            "llm": {
                "vendor": "custom",
                # Session id travels in the path, so we never depend on custom headers surviving.
                "url": f"{config.PUBLIC_BASE_URL}/s/{session_id}/chat/completions",
                "api_key": config.LLM_SHARED_SECRET,
                "greeting_message": greeting,
                "failure_message": "Maaf kijiye, ek second mein dobara try karti hoon.",
                "max_history": 20,
                "params": {"model": config.LLM_MODEL},
            },
            "tts": {"vendor": "murf", "params": tts_params},
        },
    }


async def join(body: dict) -> dict:
    url = f"{config.AGORA_API_BASE}/projects/{config.AGORA_APP_ID}/join"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=body, auth=_auth())
    if r.status_code >= 300:
        raise RuntimeError(f"Agora join failed {r.status_code}: {r.text[:500]}")
    return r.json()


async def leave(agent_id: str) -> None:
    url = f"{config.AGORA_API_BASE}/projects/{config.AGORA_APP_ID}/agents/{agent_id}/leave"
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, auth=_auth())
