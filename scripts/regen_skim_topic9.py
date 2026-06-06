"""Regenerate structural skims for stale/missing papers in topic 9 (Credit & Fixed-Income Alpha).
Retries until all papers are up-to-date. Reports progress every 5 minutes.
"""
import asyncio
import hashlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Users\zhong\source\repos\poneglyph")

from poneglyph.db import fetch_all, fetch_one, row_to_dict
from poneglyph.pipeline import _synthesize_paper

TOPIC_ID = 9
REPORT_INTERVAL = 300  # seconds


def get_stale_papers(topic, skill_hash):
    rows = fetch_all(
        """SELECT p.id, p.title, tpn.skim_skill_hash
           FROM papers p
           JOIN topic_papers tp ON p.id = tp.paper_id
           LEFT JOIN topic_paper_notes tpn ON tpn.paper_id = p.id AND tpn.topic_id = ?
           WHERE tp.topic_id = ?""",
        (TOPIC_ID, TOPIC_ID),
    )
    return [(r[0], r[1]) for r in rows if r[2] != skill_hash]


async def main():
    topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (TOPIC_ID,)))
    skill_hash = hashlib.sha256((topic["skim_skill_md"] or "").encode()).hexdigest()
    total = 181

    attempt = 0
    while True:
        stale = get_stale_papers(topic, skill_hash)
        if not stale:
            print(f"\nAll {total} papers are up-to-date. Done.")
            break

        attempt += 1
        print(f"\n--- Pass {attempt}: {len(stale)} papers remaining ---")
        last_report = time.time()
        ok = fail = 0

        for i, (pid, title) in enumerate(stale, 1):
            err = await _synthesize_paper(pid, topic)
            if err:
                fail += 1
                print(f"  FAIL [{i}/{len(stale)}] {pid}: {err}")
            else:
                ok += 1

            if time.time() - last_report >= REPORT_INTERVAL:
                remaining = len(stale) - i
                up_to_date = total - len(get_stale_papers(topic, skill_hash))
                print(f"  [5-min update] {up_to_date}/{total} up-to-date | "
                      f"this pass: {ok} ok, {fail} fail, {remaining} left in pass")
                last_report = time.time()

        up_to_date = total - len(get_stale_papers(topic, skill_hash))
        print(f"  Pass {attempt} done: {ok} ok, {fail} fail | {up_to_date}/{total} total up-to-date")

        if fail == 0 and ok == 0:
            print("  No progress made — aborting to avoid infinite loop.")
            break

        if fail > 0 and ok == 0:
            print("  All attempts failed this pass — waiting 30s before retry...")
            await asyncio.sleep(30)


asyncio.run(main())
