"""Video report service — generate MP4 slide video from report data."""
import os
import logging
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

_W, _H = 1280, 720
_BG    = (7,  11, 18)
_BG_C  = (12, 18, 32)
_CYAN  = (0, 212, 255)
_TEXT  = (226, 232, 240)
_TEXT2 = (100, 116, 139)
_RED   = (239, 68,  68)
_AMBER = (245, 158, 11)
_GREEN = (16,  185, 129)

_NOTO_PATH = "/usr/share/fonts/truetype/noto/NotoSansHebrew-Regular.ttf"
_FALLBACK  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _get_font(size: int):
    from PIL import ImageFont
    path = _NOTO_PATH if os.path.exists(_NOTO_PATH) else _FALLBACK
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _rtl(text: str) -> str:
    try:
        from bidi.algorithm import get_display
        return get_display(text)
    except Exception:
        return text


def _draw_text_ra(draw, x: int, y: int, text: str, font, fill):
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        draw.text((x - w, y), text, font=font, fill=fill)
    except AttributeError:
        w, _ = draw.textsize(text, font=font)
        draw.text((x - w, y), text, font=font, fill=fill)


def _draw_text_center(draw, x: int, y: int, text: str, font, fill):
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        draw.text((x - w // 2, y), text, font=font, fill=fill)
    except AttributeError:
        w, _ = draw.textsize(text, font=font)
        draw.text((x - w // 2, y), text, font=font, fill=fill)


def _make_slide(title: str, lines: list, slide_n: int, total: int,
                accent_color=None):
    import numpy as np
    from PIL import Image, ImageDraw

    accent = accent_color or _CYAN
    img = Image.new("RGB", (_W, _H), _BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, _W, 90], fill=_BG_C)
    draw.rectangle([0, 88, _W, 93], fill=accent)

    font_brand = _get_font(20)
    draw.text((40, 32), "Shan-AI", font=font_brand, fill=_CYAN)

    font_title = _get_font(38)
    _draw_text_center(draw, _W // 2, 24, _rtl(title), font_title, accent)

    font_body = _get_font(26)
    y = 120
    for line in lines:
        if line.startswith("---"):
            draw.rectangle([60, y + 10, _W - 60, y + 12], fill=_BG_C)
            y += 30
            continue
        col = _TEXT
        if line.startswith("🔴"):
            col = _RED
        elif line.startswith("🟡"):
            col = _AMBER
        elif line.startswith("🟢"):
            col = _GREEN
        _draw_text_ra(draw, _W - 60, y, _rtl(line), font_body, col)
        y += 46

    draw.rectangle([0, _H - 50, _W, _H], fill=_BG_C)
    font_small = _get_font(18)
    _draw_text_center(draw, _W // 2, _H - 36, f"{slide_n} / {total}", font_small, _TEXT2)
    _draw_text_ra(draw, _W - 40, _H - 36, _rtl("שן-AI • מודיעין תפעולי"), font_small, _CYAN)

    return np.array(img)


def _make_audio(text: str) -> Optional[str]:
    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang="he", slow=False)
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        tts.save(path)
        return path
    except Exception as e:
        logger.warning(f"gTTS failed: {e}")
        return None


def _slides_from_data(data: dict) -> list:
    meta = data.get("meta", {})
    es   = data.get("executive_summary", {})
    ph   = data.get("portfolio_health", {})
    rr   = data.get("risk_register", [])[:5]
    ai   = data.get("action_items", [])[:3]

    slides = []

    slides.append((
        "דוח פרויקטים שבועי",
        [
            f"נוצר: {meta.get('generated_at', '')}",
            f"עבור: {meta.get('username', '')} — {meta.get('role', '')}",
            "---",
            f"סה\"כ פרויקטים פעילים: {es.get('total_active', 0)}",
            f"🔴 באיחור: {es.get('total_delayed', 0)}   ⚠️ סיכון גבוה: {es.get('total_at_risk', 0)}",
        ],
        f"ברוכים הבאים לדוח הפרויקטים השבועי. "
        f"יש {es.get('total_active', 0)} פרויקטים פעילים, "
        f"מהם {es.get('total_delayed', 0)} באיחור ו-{es.get('total_at_risk', 0)} בסיכון גבוה.",
    ))

    rag_lines = [
        f"{'🔴 סיכון' if s=='RED' else '🟡 איחור' if s=='AMBER' else '🟢 תקין'} — {t}"
        for t, s in es.get("rag_by_type", {}).items()
    ]
    slides.append((
        "סיכום מנהלים",
        [
            f"ציון סיכון ממוצע: {es.get('avg_risk_score', 0)}",
            f"החלטות 30 ימים: {es.get('decisions_30d', 0)}  |  אחוז אישורים: {es.get('approval_rate_pct', 0)}%",
            "---",
            *rag_lines[:4],
        ],
        f"ציון הסיכון הממוצע עומד על {es.get('avg_risk_score', 0)}. "
        f"אחוז אישורי ההחלטות עומד על {es.get('approval_rate_pct', 0)} אחוז. "
        + (f"יש {es.get('critical_pending', 0)} החלטות קריטיות ממתינות." if es.get('critical_pending') else "אין החלטות קריטיות ממתינות."),
    ))

    type_lines = [
        f"{t}: {c.get('active', 0)} פעיל, {c.get('delayed', 0)} באיחור"
        for t, c in list(ph.get("type_counts", {}).items())[:4]
    ] or ["אין נתוני סוגים עדיין"]
    slides.append((
        "בריאות תיק הפרויקטים",
        type_lines,
        "סקירת תיק הפרויקטים לפי סוגים. " +
        " ".join(f"{t} — {c.get('active', 0)} פרויקטים פעילים." for t, c in list(ph.get("type_counts", {}).items())[:3]),
    ))

    risk_lines = [
        f"{'🔴' if r.get('risk_score', 0)>=70 else '🟡'} {r.get('name', '')} — ציון {r.get('risk_score', 0)}"
        for r in rr
    ] or ["אין פרויקטים בסיכון גבוה"]
    slides.append((
        "רישום סיכונים",
        risk_lines,
        "הפרויקטים בסיכון הגבוה ביותר: " +
        ", ".join(f"{r.get('name', '')} עם ציון {r.get('risk_score', 0)}" for r in rr[:3]) + ".",
    ))

    action_lines = [
        f"{'⚠️' if a.get('priority') == 'HIGH' else '🟡'} {a.get('item', '')[:60]}"
        for a in ai
    ] or ["אין פעולות נדרשות דחופות"]
    slides.append((
        "פעולות נדרשות",
        action_lines,
        "פעולות מומלצות לשבוע הבא: " +
        ". ".join(a.get("item", "")[:80] for a in ai[:3]) + ".",
    ))

    slides.append((
        "סיכום",
        [
            "נקודות עיקריות:",
            f"• {es.get('total_active', 0)} פרויקטים פעילים",
            f"• {es.get('total_delayed', 0)} פרויקטים באיחור",
            f"• {es.get('total_at_risk', 0)} פרויקטים בסיכון גבוה",
            "---",
            "Shan-AI — מודיעין תפעולי לתשתיות חשמל",
        ],
        f"לסיכום: תיק הפרויקטים מכיל {es.get('total_active', 0)} פרויקטים פעילים. "
        f"יש לטפל ב-{es.get('total_delayed', 0)} פרויקטים באיחור. "
        "תודה על הצפייה.",
    ))

    return slides


async def generate_report_video(data: dict, report_id: int) -> Optional[str]:
    """
    Generate a 720p MP4 slide video. Returns relative path like
    'project_reports/42.mp4', or None if moviepy is unavailable or fails.
    """
    try:
        from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
    except ImportError:
        logger.warning("moviepy not installed — video generation skipped")
        return None

    os.makedirs("static/project_reports", exist_ok=True)
    out_path = f"static/project_reports/{report_id}.mp4"

    slides_def = _slides_from_data(data)
    total = len(slides_def)
    slide_duration = 9
    clips = []
    tmp_audio_files = []

    try:
        for i, (title, lines, narration) in enumerate(slides_def, 1):
            arr = _make_slide(title, lines, i, total)
            img_clip = ImageClip(arr, duration=slide_duration)

            audio_path = _make_audio(narration)
            if audio_path:
                tmp_audio_files.append(audio_path)
                try:
                    audio_clip = AudioFileClip(audio_path)
                    if audio_clip.duration > slide_duration:
                        audio_clip = audio_clip.subclip(0, slide_duration)
                    img_clip = img_clip.set_audio(audio_clip)
                except Exception as ae:
                    logger.warning(f"audio attach failed for slide {i}: {ae}")

            clips.append(img_clip)

        final = concatenate_videoclips(clips, method="compose")
        final.write_videofile(
            out_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            logger=None,
            verbose=False,
        )
        return f"project_reports/{report_id}.mp4"

    except Exception as e:
        logger.error(f"video generation failed: {e}")
        return None

    finally:
        for p in tmp_audio_files:
            try:
                os.unlink(p)
            except Exception:
                pass
