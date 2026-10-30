"""Redis Streams transport; PostgreSQL job rows remain the source of truth."""

import json
import os


def _stream_name() -> str:
    return os.environ.get("EXTRACTION_QUEUE_STREAM", "alttext:extraction")


def _group_name() -> str:
    return os.environ.get("EXTRACTION_QUEUE_GROUP", "render-workers")


async def publish_extraction_job(job_id: str) -> bool:
    """Best-effort publish. Failed publish is recovered from queued DB rows."""
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return False
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        await client.xadd(_stream_name(), {"job_id": job_id}, maxlen=100_000, approximate=True)
        return True
    finally:
        await client.aclose()


def queue_settings():
    return _stream_name(), _group_name()
