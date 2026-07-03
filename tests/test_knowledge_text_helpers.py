"""Unit tests for knowledge_service pure text helpers — the Hebrew
normalization/keyword pipeline every question passes through. No DB, no LLM."""
from unittest.mock import MagicMock

from app.services.knowledge_service import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    _dedup_fragment_lines,
    _expand_hebrew_abbrevs,
    _extract_keywords,
    _extract_project_name,
    _extract_query_phrases,
    _has_proper_nouns,
    _inject_domain_keywords,
    _question_word_forms,
    _rerank_by_query_keywords,
    _word_forms,
    chunk_text,
    normalize_hebrew,
)


# ── normalize_hebrew ─────────────────────────────────────────────────────────

def test_normalize_hebrew_strips_nikud():
    assert normalize_hebrew("שָׁלוֹם") == "שלומ"


def test_normalize_hebrew_converts_final_letters():
    assert normalize_hebrew("ךםןףץ") == "כמנפצ"


def test_normalize_hebrew_lowercases_latin():
    assert normalize_hebrew("WBS-4711") == "wbs-4711"


def test_normalize_hebrew_plain_text_unchanged():
    assert normalize_hebrew("בית הגדי") == "בית הגדי"


# ── word forms ───────────────────────────────────────────────────────────────

def test_word_forms_strips_single_prefix():
    # ה prefix stripped: הפרויקט → also פרויקט
    assert _word_forms("הפרויקט") == {"הפרויקט", "פרויקט"}


def test_word_forms_short_word_no_strip():
    # 2-char words are never prefix-stripped
    assert _word_forms("של") == {"של"}


def test_question_word_forms_union_over_words():
    forms = _question_word_forms("מה השלב")
    assert "השלב" in forms
    assert "שלב" in forms  # prefix-stripped form
    assert "מה" in forms


# ── _expand_hebrew_abbrevs ───────────────────────────────────────────────────

def test_expand_abbrevs_whole_token_only():
    # Regression: single-letter entries ('פ', 'מ', 'ב') must not rewrite
    # characters inside other words — str.replace garbled every question.
    q = "מה שלב פרויקט חולה?"
    assert _expand_hebrew_abbrevs(q) == q


def test_expand_abbrevs_expands_standalone_token():
    assert "מנהל פרויקט" in _expand_hebrew_abbrevs("מי פ של הפרויקט?")


def test_expand_abbrevs_hishmul_spelling():
    # Data stores 'חשמול' (no י); users write 'חישמול'
    assert _expand_hebrew_abbrevs("מתי חישמול תחנת גליל?") == "מתי חשמול תחנת גליל?"


# ── chunk_text ───────────────────────────────────────────────────────────────

def test_chunk_text_empty_returns_empty_list():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_chunk_text_short_text_single_chunk():
    assert chunk_text("hello world") == ["hello world"]


def test_chunk_text_long_text_overlaps():
    text = "א" * (CHUNK_SIZE + 50)
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert len(chunks[0]) == CHUNK_SIZE
    # Second chunk starts CHUNK_SIZE - CHUNK_OVERLAP into the text
    assert len(chunks[1]) == 50 + CHUNK_OVERLAP


def test_chunk_text_custom_sizes():
    chunks = chunk_text("abcdefghij", chunk_size=4, overlap=2)
    assert chunks[0] == "abcd"
    assert chunks[1] == "cdef"
    assert all(len(c) <= 4 for c in chunks)


# ── keyword extraction ───────────────────────────────────────────────────────

def test_extract_keywords_years_and_hebrew():
    kws = _extract_keywords("אילו פרויקטים יסתיימו ב-2025?")
    assert "2025" in kws
    assert "פרויקטים" in kws


def test_extract_keywords_no_keywords():
    assert _extract_keywords("what is this") == []


def test_extract_query_phrases_bat_yam_problem():
    # 2-char words like 'בת' and 'ים' must survive extraction (the "Bat Yam
    # problem"). The greedy multi-word regex may fold them into a longer
    # window, so they are guaranteed via the singles list (len >= 2).
    phrases = _extract_query_phrases("מה קורה בפרויקט בת ים?")
    assert "בת" in phrases
    assert "ים" in phrases
    # When the phrase stands alone the extractor emits it whole
    assert "בת ים" in _extract_query_phrases("בת ים")
    # Longest-first ordering: most specific match first
    assert phrases == sorted(phrases, key=len, reverse=True)


def test_inject_domain_keywords_risk_trigger():
    out = _inject_domain_keywords("אילו פרויקטים בסיכון?", ["סיכון"])
    assert "חסם" in out
    assert "גבוה" in out


def test_inject_domain_keywords_year_adds_date_terms():
    out = _inject_domain_keywords("פרויקטים של 2025", ["2025"])
    assert "יעד" in out
    assert "תאריך" in out


def test_inject_domain_keywords_no_trigger_passthrough():
    out = _inject_domain_keywords("שאלה כללית", ["מילה"])
    assert set(out) == {"מילה"}


def test_has_proper_nouns_filters_common_words():
    assert _has_proper_nouns(["חולה", "פרויקט", "כמה"]) == ["חולה"]


def test_has_proper_nouns_rejects_long_and_latin():
    assert _has_proper_nouns(["abcd", "מילהארוכהמאודמאוד"]) == []


# ── rerank ───────────────────────────────────────────────────────────────────

def _chunk(content: str):
    c = MagicMock()
    c.content = content
    return c


def test_rerank_moves_keyword_hits_first():
    miss = _chunk("שורה על משהו אחר לגמרי")
    hit = _chunk("הפרויקט יסתיים בשנת 2025 כמתוכנן")
    ranked = _rerank_by_query_keywords([miss, hit], "מה יקרה ב-2025?")
    assert ranked[0] is hit
    assert ranked[1] is miss


def test_rerank_no_keywords_preserves_order():
    a, b = _chunk("a"), _chunk("b")
    assert _rerank_by_query_keywords([a, b], "?? !!") == [a, b]


# ── chunk metadata helpers ───────────────────────────────────────────────────

def test_extract_project_name_master_format():
    assert _extract_project_name("🏗️ Project: בית הגדי | WBS: BG-04") == "בית הגדי"


def test_extract_project_name_missing():
    assert _extract_project_name("שורה בלי תווית") is None


def test_dedup_fragment_lines_removes_duplicates():
    frags = [
        "שורה א\nשורה ב",
        "שורה א\nשורה ב",   # exact duplicate chunk
        "שורה ב\nשורה ג",   # overlapping line
    ]
    assert _dedup_fragment_lines(frags) == ["שורה א", "שורה ב", "שורה ג"]
