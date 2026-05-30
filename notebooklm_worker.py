"""
NotebookLM Audio Worker
=======================
Runs locally. Polls Railway DB every 60s for project_reports with video_path IS NULL.
For each pending report:
  1. Adds report text as source to the fixed NotebookLM notebook
  2. Generates Hebrew audio overview
  3. Downloads audio to static/project_reports/{id}.m4a
  4. Updates DB: video_path + notebooklm_url

Run with: python notebooklm_worker.py
"""
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import asyncpg
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nlm_worker")

# ── Config ────────────────────────────────────────────────────────────────────

NOTEBOOK_URL = "https://notebooklm.google.com/notebook/a53d8510-4e0a-4f45-9729-cd2ac50687eb"
STATE_FILE   = r"C:\Users\livya\AppData\Local\notebooklm-mcp\Data\browser_state\state.json"
DEST_DIR     = Path(r"C:\Users\livya\Desktop\SHAN-AI\static\project_reports")
DEST_DIR.mkdir(parents=True, exist_ok=True)

# Both DBs — worker updates Railway (production) AND local Docker
RAILWAY_DB   = "postgresql://shan_user:shan_secure_pass_2025@interchange.proxy.rlwy.net:15720/shan_ai"
LOCAL_DB     = "postgresql://shan_user:shan_secure_pass@localhost:5432/shan_ai"

POLL_INTERVAL = 60   # seconds between DB polls
AUDIO_TIMEOUT = 600  # seconds to wait for audio generation


# ── Report text extraction ────────────────────────────────────────────────────

def _build_report_text(report_id: int, report_data: dict) -> str:
    meta = report_data.get("meta", {})
    es   = report_data.get("executive_summary", {})
    rr   = report_data.get("risk_register", [])
    dd   = report_data.get("delayed_detail", [])

    lines = [
        f"דוח פרויקטים — Shan-AI — {meta.get('generated_at', '')}",
        f"מנהל: {meta.get('username', '')} | תפקיד: {meta.get('role', '')}",
        "",
        "=== סיכום מנהלים ===",
        f"פרויקטים פעילים: {es.get('total_active', 0)}",
        f"באיחור: {es.get('total_delayed', 0)}",
        f"סיכון גבוה: {es.get('total_at_risk', 0)}",
        f"צפויים לסיכון שבוע הבא: {es.get('entering_next_week', 0)}",
        f"ציון סיכון ממוצע: {es.get('avg_risk_score', 0)}",
        f"אחוז אישורי החלטות: {es.get('approval_rate_pct', 0)}%",
        f"החלטות 30 יום: {es.get('decisions_30d', 0)}",
        f"קריטיות ממתינות: {es.get('critical_pending', 0)}",
        "",
    ]

    if rr:
        lines.append("=== רישום סיכונים ===")
        for r in rr[:10]:
            lines.append(f"• {r.get('name','')} — ציון: {r.get('risk_score',0)} — {r.get('main_reason','')}")
        lines.append("")

    if dd:
        lines.append("=== פרויקטים באיחור ===")
        for r in dd[:15]:
            lines.append(
                f"• {r.get('name','')} ({r.get('type','')}) — "
                f"{r.get('days_overdue',0)} ימי איחור — {r.get('main_reason','')}"
            )
        lines.append("")

    ai = report_data.get("action_items", [])
    if ai:
        lines.append("=== פעולות נדרשות ===")
        for a in ai:
            lines.append(f"• [{a.get('priority','')}] {a.get('item','')}")

    return "\n".join(lines)


# ── NotebookLM Playwright helpers ─────────────────────────────────────────────

async def _add_source_to_notebook(page, text: str, title: str) -> bool:
    """Add text source to the notebook. Returns True on success."""
    try:
        # Click "+ Add sources"
        add_btn = page.locator("button, [role='button']").filter(has_text="Add sources").first
        if await add_btn.count() == 0:
            add_btn = page.locator("button").filter(has_text="הוסף מקורות").first
        await add_btn.click(timeout=10000)
        await page.wait_for_timeout(1500)

        # Click "Paste text" or similar
        paste_opt = page.locator("button, [role='menuitem'], [role='option']").filter(has_text="Paste text")
        if await paste_opt.count() == 0:
            paste_opt = page.locator("button, [role='menuitem']").filter(has_text="text")
        if await paste_opt.count() > 0:
            await paste_opt.first.click()
            await page.wait_for_timeout(1000)

        # Fill in title and text
        title_input = page.locator("input[placeholder*='title' i], input[placeholder*='כותרת' i]").first
        if await title_input.count() > 0:
            await title_input.fill(title)

        text_area = page.locator("textarea").first
        if await text_area.count() > 0:
            await text_area.fill(text)

        # Click "Insert" or "Add"
        insert_btn = page.locator("button").filter(has_text="Insert").or_(
            page.locator("button").filter(has_text="Add")
        ).or_(
            page.locator("button").filter(has_text="הוסף")
        ).last
        await insert_btn.click(timeout=10000)
        await page.wait_for_timeout(3000)
        log.info("Source added successfully")
        return True

    except Exception as e:
        log.warning(f"add_source failed: {e}")
        return False


async def _generate_audio(page, custom_prompt: str) -> bool:
    """Trigger audio overview generation with Hebrew custom prompt."""
    try:
        # Click "Audio..." in Studio panel
        audio_btn = page.locator("studio-sidebar button, .studio-panel button").filter(has_text="Audio")
        if await audio_btn.count() == 0:
            audio_btn = page.locator("button").filter(has_text="Audio...")
        await audio_btn.first.click(timeout=10000)
        await page.wait_for_timeout(1500)

        # Try to find "Customize" or just click Generate directly
        customize = page.locator("button").filter(has_text="Customize")
        if await customize.count() > 0:
            await customize.first.click()
            await page.wait_for_timeout(1000)
            prompt_box = page.locator("textarea").last
            if await prompt_box.count() > 0:
                await prompt_box.fill(custom_prompt)
            await page.wait_for_timeout(500)

        # Click Generate
        gen_btn = page.locator("button").filter(has_text="Generate").or_(
            page.locator("button").filter(has_text="צור")
        ).last
        if await gen_btn.count() > 0:
            await gen_btn.click(timeout=10000)
            await page.wait_for_timeout(2000)
            log.info("Audio generation triggered")
            return True

        log.warning("Could not find Generate button")
        return False

    except Exception as e:
        log.warning(f"generate_audio failed: {e}")
        return False


async def _wait_for_audio_ready(page, timeout_s: int = 600) -> bool:
    """Poll until audio tile appears as ready (not 'Generating...')."""
    for i in range(timeout_s // 15):
        await page.wait_for_timeout(15000)
        generating = await page.locator("text=Generating").count()
        if generating == 0:
            artifact = await page.locator("artifact-library-item").count()
            if artifact > 0:
                log.info("Audio ready")
                return True
        log.info(f"Audio still generating... ({(i+1)*15}s)")
    return False


async def _download_audio(page, dest: Path) -> bool:
    """Download the audio file using JS-click on the hidden more_vert menu."""
    try:
        audio_item = page.locator("artifact-library-item").first
        if await audio_item.count() == 0:
            return False

        await audio_item.scroll_into_view_if_needed()
        await audio_item.hover()
        await page.wait_for_timeout(800)

        more_icons = page.locator("mat-icon").filter(has_text="more_vert")
        if await more_icons.count() == 0:
            return False

        all_icons = await more_icons.all()
        target = all_icons[-1]
        await target.evaluate("el => el.closest('button') ? el.closest('button').click() : el.click()")
        await page.wait_for_timeout(1200)

        dl_item = page.locator("[role='menuitem'], mat-menu-item").filter(has_text="ownload")
        if await dl_item.count() == 0:
            dl_item = page.locator("button").filter(has_text="ownload")
        if await dl_item.count() == 0:
            return False

        async with page.expect_download(timeout=60000) as dl_info:
            await dl_item.first.click()
        dl = await dl_info.value
        await dl.save_as(str(dest))
        log.info(f"Audio saved: {dest}")
        return True

    except Exception as e:
        log.warning(f"download_audio failed: {e}")
        return False


async def process_report(report_id: int, report_data: dict) -> tuple[str | None, str]:
    """Full pipeline: add source → generate audio → download. Returns (file_path, notebook_url)."""
    report_text = _build_report_text(report_id, report_data)
    meta = report_data.get("meta", {})
    title = f"דוח פרויקטים {meta.get('generated_at', str(report_id))}"
    dest  = DEST_DIR / f"{report_id}.m4a"

    hebrew_prompt = (
        "צור שידור מדוברים בעברית בלבד — שני דוברים ישראלים מנוסים דנים בדוח. "
        "התמקד ב: מספר הפרויקטים הפעילים, האיחורים והסיבות שלהם, "
        "ציוני הסיכון הגבוהים ביותר, הפעולות הנדרשות דחופות, "
        "ותחזית לשבוע הבא. דבר בעברית בלבד."
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx  = await browser.new_context(storage_state=STATE_FILE)
        page = await ctx.new_page()

        log.info(f"Navigating to notebook for report {report_id}...")
        await page.goto(NOTEBOOK_URL, wait_until="load", timeout=90000)
        await page.wait_for_timeout(3000)

        # Add source
        source_added = await _add_source_to_notebook(page, report_text, title)
        if not source_added:
            log.warning(f"Could not add source for report {report_id}, skipping audio gen")
            await browser.close()
            return None, NOTEBOOK_URL

        # Generate audio
        await _generate_audio(page, hebrew_prompt)

        # Wait for it to be ready
        ready = await _wait_for_audio_ready(page, timeout_s=AUDIO_TIMEOUT)
        if not ready:
            log.error(f"Audio timeout for report {report_id}")
            await browser.close()
            return None, NOTEBOOK_URL

        # Download
        success = await _download_audio(page, dest)
        await browser.close()

        if success:
            return str(dest.relative_to(Path(r"C:\Users\livya\Desktop\SHAN-AI\static"))).replace("\\", "/"), NOTEBOOK_URL

    return None, NOTEBOOK_URL


# ── DB polling loop ───────────────────────────────────────────────────────────

async def update_db(conn, report_id: int, video_path: str, notebooklm_url: str):
    await conn.execute(
        "UPDATE project_reports SET video_path=$1, notebooklm_url=$2 WHERE id=$3",
        video_path, notebooklm_url, report_id,
    )
    log.info(f"DB updated: report {report_id} → {video_path}")


async def run_worker():
    log.info("NotebookLM worker started. Connecting to Railway DB...")

    while True:
        try:
            conn = await asyncpg.connect(RAILWAY_DB)
            rows = await conn.fetch(
                "SELECT id, report_data FROM project_reports "
                "WHERE video_path IS NULL ORDER BY generated_at DESC LIMIT 5"
            )
            if rows:
                log.info(f"Found {len(rows)} pending reports")
                for row in rows:
                    rid = row["id"]
                    data = row["report_data"]
                    if isinstance(data, str):
                        data = json.loads(data)
                    log.info(f"Processing report {rid}...")
                    path, nb_url = await process_report(rid, data)
                    if path:
                        await update_db(conn, rid, path, nb_url)
                    else:
                        # Mark notebooklm_url so we don't retry endlessly
                        await conn.execute(
                            "UPDATE project_reports SET notebooklm_url=$1 WHERE id=$2 AND notebooklm_url IS NULL",
                            nb_url, rid,
                        )
            else:
                log.info("No pending reports")

            await conn.close()

        except Exception as e:
            log.error(f"Worker error: {e}")

        log.info(f"Sleeping {POLL_INTERVAL}s...")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_worker())
