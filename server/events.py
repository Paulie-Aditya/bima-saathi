"""WebSocket hub. Browsers subscribe per member; every event is also written to the events table
so a run can be replayed for the backup video (Plan D)."""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from . import db


class Hub:
    def __init__(self) -> None:
        self.subs: dict[str, set[WebSocket]] = defaultdict(set)
        self.lock = asyncio.Lock()

    async def join(self, key: str, ws: WebSocket) -> None:
        async with self.lock:
            self.subs[key].add(ws)

    async def leave(self, key: str, ws: WebSocket) -> None:
        async with self.lock:
            self.subs[key].discard(ws)

    async def emit(self, member_id: str | None, event: dict[str, Any], session_id: str | None = None) -> None:
        event = {**event, "ts": event.get("ts", time.time())}
        if session_id:
            event["session_id"] = session_id
        db.run("INSERT INTO events(session_id, member_id, type, payload, ts) VALUES (?,?,?,?,?)",
               session_id, member_id, event["type"], json.dumps(event, ensure_ascii=False), event["ts"])
        targets = list(self.subs.get(member_id or "", set())) + list(self.subs.get("*", set()))
        dead = []
        for ws in targets:
            try:
                await ws.send_text(json.dumps(event, ensure_ascii=False))
            except Exception:
                dead.append(ws)
        for ws in dead:
            for key in list(self.subs):
                self.subs[key].discard(ws)


hub = Hub()
