"""One-shot seed (compose `seed` service).

Applies the schema, the demo book, the fixture documents (queued for ingestion) and the fixture emails (held
until released). Idempotent — safe to re-run on every `docker compose up`.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from producery.logging_setup import setup_logging  # noqa: E402
from producery.settings import get_settings  # noqa: E402
from producery.store.db import Database  # noqa: E402
from producery.store.records import Records  # noqa: E402

FRONT = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)


def parse_doc(path: Path) -> dict:
    """Parse a fixture markdown file with `---` front matter into `{id, title, doc_type, body}`.

    Args:
        path: The markdown file.

    Raises:
        ValueError: When the file has no front matter.
    """
    m = FRONT.match(path.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{path}: missing front matter")
    meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    return {
        "id": meta["id"],
        "title": meta["title"],
        "doc_type": meta["doc_type"],
        "body": m.group(2).strip(),
    }


async def main() -> int:
    """Apply the schema and demo book, upsert fixture documents and emails, enqueue ingestion for new ones.

    Ingestion jobs are enqueued through arq only for documents whose upsert reported a change, with job id
    `ingest:<doc_id>`. Returns 0.
    """
    s = get_settings()
    log = setup_logging("seed")
    db = Database(s.database_url, max_size=2)
    await db.open()
    await db.apply_sql((ROOT / "producery" / "store" / "schema.sql").read_text(encoding="utf-8"))
    await db.apply_sql((ROOT / "fixtures" / "book.sql").read_text(encoding="utf-8"))
    rec = Records(db)

    queued: list[str] = []
    for path in sorted((ROOT / "fixtures" / "documents").glob("*.md")):
        d = parse_doc(path)
        if await rec.upsert_document(d["id"], d["title"], d["doc_type"], d["body"]):
            queued.append(d["id"])
    for line in (ROOT / "fixtures" / "emails.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            await rec.upsert_email(json.loads(line), status="held")

    # queue ingestion jobs for new/changed documents (background worker)
    if queued:
        from arq import create_pool
        from arq.connections import RedisSettings

        redis = await create_pool(RedisSettings.from_dsn(s.redis_url))
        for doc_id in queued:
            await redis.enqueue_job("ingest_document", doc_id, _job_id=f"ingest:{doc_id}")
        await redis.close()
    log.info("seed_done documents_queued=%s", queued)
    await db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
