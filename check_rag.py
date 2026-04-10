import asyncio
from sqlalchemy import select, func
from app.database import async_session_maker
from app.models import KnowledgeFile, Decision  # ודא שהשמות תואמים ל-models.py שלך

async def diagnose_rag():
    print("--- Shan-AI RAG Diagnosis Start ---")

    async with async_session_maker() as session:
        # 1. בדיקת קבצים וסטטוסים
        files_result = await session.execute(select(KnowledgeFile))
        files = files_result.scalars().all()

        print(f"\nFound {len(files)} files in system:")
        for f in files:
            print(f"- File: {f.original_name} | Status: {f.status} | Chunks: {f.chunk_count}")

        # 2. שליפת דוגמאות מתוך ה-Chunks
        from sqlalchemy import text
        try:
            chunks_query = text("SELECT id, chunk_idx, content FROM knowledge_chunks ORDER BY id DESC LIMIT 5")
            chunks_result = await session.execute(chunks_query)
            chunks = chunks_result.fetchall()

            print("\n--- Latest 5 Chunks Preview ---")
            for i, row in enumerate(chunks):
                chunk_id, chunk_idx, content = row
                print(f"\n[Chunk {i+1} (ID: {chunk_id}, Index: {chunk_idx})]:")
                print(f"Content: {content[:300]}...") # מציג רק את תחילת הטקסט
        except Exception as e:
            print(f"\nCould not fetch chunks directly: {e}")
            print("Note: Check the knowledge_chunks table structure.")

    print("\n--- Diagnosis End ---")

if __name__ == "__main__":
    asyncio.run(diagnose_rag())
