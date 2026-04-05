"""Knowledge base service — file ingestion, chunking, embedding, and RAG search."""

import asyncio
import logging
import os
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import KnowledgeFile, KnowledgeChunk
from app.services.embedding_service import embed
from app.database import async_session_maker

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path("uploads")
CHUNK_SIZE = 600
CHUNK_OVERLAP = 100


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(file_path: str, file_type: str) -> str:
    """Extract plain text from PDF, DOCX, or XLSX file."""
    path = Path(file_path)

    if file_type == "pdf":
        import pdfplumber
        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n\n".join(text_parts)

    elif file_type == "docx":
        from docx import Document
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    elif file_type == "xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        rows = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                row_text = " | ".join(str(c) for c in row if c is not None)
                if row_text.strip():
                    rows.append(row_text)
        return "\n".join(rows)

    return ""


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start += chunk_size - overlap
    return chunks


# ---------------------------------------------------------------------------
# AI summary (one-liner via Groq)
# ---------------------------------------------------------------------------

async def _generate_summary(text_snippet: str, filename: str) -> str:
    """Generate a short Hebrew summary of the file using Groq."""
    try:
        from app.services.groq_client import groq_chat
        snippet = text_snippet[:2000]
        return await groq_chat(
            messages=[
                {
                    "role": "system",
                    "content": "סכם את תוכן המסמך בעברית במשפט אחד קצר (עד 20 מילים).",
                },
                {
                    "role": "user",
                    "content": f"שם קובץ: {filename}\n\nתוכן:\n{snippet}",
                },
            ],
            max_tokens=80,
            temperature=0.2,
        )
    except Exception as e:
        logger.warning(f"Summary generation failed: {e}")
        return filename


# ---------------------------------------------------------------------------
# Main processing pipeline
# ---------------------------------------------------------------------------

async def process_file(file_id: int) -> None:
    """Extract, chunk, embed, and store all chunks for a KnowledgeFile record."""
    async with async_session_maker() as session:
        kf = await session.get(KnowledgeFile, file_id)
        if not kf:
            return

        try:
            # 1. Extract text
            raw_text = await asyncio.get_event_loop().run_in_executor(
                None, extract_text, kf.file_path, kf.file_type
            )
            if not raw_text.strip():
                kf.status = "error"
                kf.summary = "לא נמצא טקסט בקובץ"
                await session.commit()
                return

            # 2. Generate AI summary from first 2000 chars
            kf.summary = await _generate_summary(raw_text[:2000], kf.original_name)

            # 3. Chunk
            chunks = chunk_text(raw_text)

            # 4. Embed each chunk and store
            for idx, chunk_content in enumerate(chunks):
                vector = await embed(chunk_content)
                chunk = KnowledgeChunk(
                    file_id=file_id,
                    chunk_idx=idx,
                    content=chunk_content,
                    embedding=vector,
                )
                session.add(chunk)

            kf.chunk_count = len(chunks)
            kf.status = "ready"
            await session.commit()
            logger.info(f"Processed file {file_id}: {len(chunks)} chunks")

        except Exception as e:
            logger.error(f"Error processing file {file_id}: {e}", exc_info=True)
            try:
                kf.status = "error"
                kf.summary = f"שגיאה בעיבוד: {str(e)[:100]}"
                await session.commit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search_knowledge(query: str, session: AsyncSession, limit: int = 5) -> list[KnowledgeChunk]:
    """Find the most relevant knowledge chunks for a query using cosine similarity."""
    try:
        query_vector = await embed(query)
        stmt = (
            select(KnowledgeChunk)
            .join(KnowledgeFile, KnowledgeChunk.file_id == KnowledgeFile.id)
            .where(KnowledgeFile.status == "ready")
            .where(KnowledgeChunk.embedding.isnot(None))
            .order_by(KnowledgeChunk.embedding.cosine_distance(query_vector))
            .limit(limit)
        )
        result = await session.execute(stmt)
        return result.scalars().all()
    except Exception as e:
        logger.warning(f"Knowledge search failed: {e}")
        return []


def format_knowledge_context(chunks: list[KnowledgeChunk]) -> str:
    """Format knowledge chunks as context string for the AI."""
    if not chunks:
        return ""
    lines = ["מידע רלוונטי ממסמכי הארגון:"]
    for chunk in chunks:
        lines.append(f"• {chunk.content}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Decisions context for Q&A
# ---------------------------------------------------------------------------

async def get_decisions_context(session: AsyncSession, user_id: int) -> str:
    """Fetch recent decisions submitted by this user and format as Q&A context."""
    from app.models import Decision
    stmt = (
        select(Decision)
        .where(Decision.submitter_id == user_id)
        .order_by(Decision.created_at.desc())
        .limit(20)
    )
    result = await session.execute(stmt)
    decisions = result.scalars().all()
    if not decisions:
        return ""
    lines = ["החלטות אחרונות של המשתמש:"]
    for d in decisions:
        date_str = d.created_at.strftime("%d/%m/%Y") if d.created_at else "—"
        lines.append(
            f"• [{d.type.value.upper()} | {d.status.value}] {d.summary or '—'} | "
            f"פעולה: {d.recommended_action or '—'} | תאריך: {date_str}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Q&A
# ---------------------------------------------------------------------------

async def answer_question(question: str, context: str) -> str:
    """Use Groq to answer a question based on knowledge context."""
    try:
        from app.services.groq_client import groq_chat

        system_prompt = (
            "אתה עוזר ארגוני חכם. ענה על השאלה בעברית בלבד, "
            "בהתבסס על המידע שסופק. אם המידע אינו מספיק, אמור זאת בבירור. "
            "תשובה קצרה וממוקדת — עד 5 משפטים."
        )

        return await groq_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{context}\n\nשאלה: {question}"},
            ],
            max_tokens=400,
            temperature=0.3,
        )
    except Exception as e:
        logger.error(f"answer_question failed: {e}")
        return "שגיאה בעיבוד השאלה. נסה שוב מאוחר יותר."


async def answer_with_full_context(question: str, session: AsyncSession, user_id: int) -> dict:
    """Search knowledge base + decisions, then answer. Returns answer + source details."""
    chunks = await search_knowledge(question, session, limit=5)
    decisions_ctx = await get_decisions_context(session, user_id)

    parts = []
    if chunks:
        parts.append(format_knowledge_context(chunks))
    if decisions_ctx:
        parts.append(decisions_ctx)

    if not parts:
        return {
            "answer": "לא נמצא מידע רלוונטי. העלה קבצים או הגש החלטות תחילה.",
            "has_files": False,
            "has_decisions": False,
            "file_names": [],
            "sources_text": "",
        }

    combined = "\n\n".join(parts)
    answer = await answer_question(question, combined)

    # Collect unique file names used
    file_names = []
    if chunks:
        seen = set()
        for chunk in chunks:
            if chunk.file_id not in seen:
                seen.add(chunk.file_id)
                kf = await session.get(KnowledgeFile, chunk.file_id)
                if kf:
                    file_names.append(kf.original_name)

    # Build a short sources line
    source_parts = []
    if decisions_ctx:
        source_parts.append("📋 מסד ההחלטות")
    if file_names:
        source_parts.append("📁 " + " | ".join(file_names))
    sources_text = "מקורות: " + " · ".join(source_parts) if source_parts else ""

    return {
        "answer": answer,
        "has_files": bool(chunks),
        "has_decisions": bool(decisions_ctx),
        "file_names": file_names,
        "sources_text": sources_text,
    }
