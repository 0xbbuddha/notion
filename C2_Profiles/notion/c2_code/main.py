#!/usr/bin/env python3
"""
Notion C2 Profile - Server
Polls the Notion database for messages from agents, forwards them to the
Mythic server, and posts the responses back to Notion.

Config is read from ./config.json (written by Mythic at container startup).
Environment variables are used as fallback for local development.
"""

import asyncio
import json
import logging
import os
import random
import sys
from pathlib import Path

import httpx

from notion_client import NotionClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("notion-c2-server")

# Mythic's internal HTTP endpoint for processing agent messages.
# Reachable within the Mythic Docker network.
MYTHIC_SERVER_HOST = os.environ.get("MYTHIC_SERVER_HOST", "mythic_server")
MYTHIC_SERVER_PORT = int(os.environ.get("MYTHIC_SERVER_PORT", "17443"))
MYTHIC_AGENT_URL = f"http://{MYTHIC_SERVER_HOST}:{MYTHIC_SERVER_PORT}/agent_message"


def load_config() -> dict:
    """
    Load runtime configuration.
    Mythic writes the profile parameters to <c2_code>/config.json
    before starting the server binary.
    """
    # --- Diagnostic: log what's actually on disk ---
    here = Path(__file__).parent
    logger.info(f"Server started — CWD: {Path.cwd()}, __file__: {__file__}")
    logger.info(f"c2_code contents: {[p.name for p in here.iterdir()]}")
    # Search for any config.json written by Mythic anywhere under /Mythic/
    import glob
    all_configs = glob.glob("/Mythic/**/config.json", recursive=True)
    logger.info(f"All config.json found under /Mythic/: {all_configs}")

    config_path = here / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            data = json.load(f)
        # Mythic sometimes wraps params under an "instances" key
        if "instances" in data and isinstance(data["instances"], list) and data["instances"]:
            data = data["instances"][0]
        logger.info("Config loaded from config.json")
        return data

    logger.warning(
        f"config.json not found at {config_path}. "
        "Create it from config.json.example or set NOTION_TOKEN / NOTION_DB_ID env vars."
    )
    return {
        "integration_token": os.environ.get("NOTION_TOKEN", ""),
        "database_id": os.environ.get("NOTION_DB_ID", ""),
        "callback_interval": int(os.environ.get("CALLBACK_INTERVAL", "10")),
        "callback_jitter": int(os.environ.get("CALLBACK_JITTER", "10")),
    }


async def forward_to_mythic(data: bytes) -> bytes:
    """
    POST raw agent bytes to Mythic's internal API.
    Mythic decrypts and processes the message, then returns the encrypted response.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            MYTHIC_AGENT_URL,
            content=data,
            headers={
                "Content-Type": "application/octet-stream",
                "mythic": "notion",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.content


def compute_sleep(interval: int, jitter: int) -> float:
    """Return the sleep duration with jitter applied."""
    if jitter <= 0:
        return float(interval)
    delta = interval * (jitter / 100.0)
    return max(1.0, interval + random.uniform(-delta, delta))


async def poll_loop(notion: NotionClient, interval: int, jitter: int) -> None:
    """
    Main loop:
      1. Query Notion for unprocessed inbound messages (direction="in").
      2. For each message, read the payload and forward it to Mythic.
      3. Post Mythic's response back to Notion (direction="out").
      4. Mark the original message as processed.
    """
    logger.info(
        f"Starting poll loop (interval={interval}s, jitter={jitter}%, "
        f"mythic={MYTHIC_AGENT_URL})"
    )

    while True:
        try:
            pages = await notion.query_pending(direction="in")
            if pages:
                logger.debug(f"Found {len(pages)} pending message(s)")

            for page in pages:
                page_id = page["id"]
                agent_id = NotionClient.get_agent_id(page)

                if not agent_id:
                    logger.warning(f"Page {page_id} has no agent_id, skipping")
                    await notion.mark_processed(page_id)
                    continue

                logger.info(f"[{agent_id}] Processing inbound message (page {page_id})")

                try:
                    data = await notion.read_message_data(page_id)
                    logger.debug(f"[{agent_id}] Read {len(data)} bytes from Notion")

                    response = await forward_to_mythic(data)
                    logger.info(
                        f"[{agent_id}] Received {len(response)} bytes from Mythic"
                    )

                    await notion.create_response_page(agent_id, response, direction="out")
                    logger.info(f"[{agent_id}] Response posted to Notion")

                except Exception as e:
                    logger.error(
                        f"[{agent_id}] Failed to process message {page_id}: {e}",
                        exc_info=True,
                    )
                    # Don't mark as processed so it can be retried next cycle.
                    continue

                await notion.mark_processed(page_id)

        except Exception as e:
            logger.error(f"Error in poll loop: {e}", exc_info=True)

        try:
            stale = await notion.query_processed_out()
            for page in stale:
                await notion.archive_page(page["id"])
            if stale:
                logger.debug(f"Archived {len(stale)} processed outbound page(s)")
        except Exception as e:
            logger.error(f"Error during outbound cleanup: {e}", exc_info=True)

        await asyncio.sleep(compute_sleep(interval, jitter))


async def main() -> None:
    config = load_config()

    token = config.get("integration_token", "")
    db_id = config.get("database_id", "")
    interval = int(config.get("callback_interval", 10))
    jitter = int(config.get("callback_jitter", 10))

    if not token:
        logger.error("integration_token is missing from config")
        sys.exit(1)
    if not db_id:
        logger.error("database_id is missing from config")
        sys.exit(1)

    notion = NotionClient(token=token, database_id=db_id)
    await poll_loop(notion, interval, jitter)


if __name__ == "__main__":
    asyncio.run(main())
