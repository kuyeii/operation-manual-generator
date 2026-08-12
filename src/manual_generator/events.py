from __future__ import annotations

import asyncio
import json
from collections import defaultdict


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict]]] = defaultdict(set)

    async def publish(self, task_id: str, event: str, data: dict) -> None:
        payload = {"event": event, "data": data}
        for queue in list(self._subscribers[task_id]):
            await queue.put(payload)

    async def stream(self, task_id: str):
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._subscribers[task_id].add(queue)
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: {payload['event']}\ndata: {json.dumps(payload['data'], ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self._subscribers[task_id].discard(queue)


broker = EventBroker()

