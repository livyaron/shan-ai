"""Re-process the latest uploaded knowledge file with new RAG improvements."""
import asyncio
from sqlalchemy import delete
from app.database import async_session_maker
from app.models import KnowledgeFile, KnowledgeChunk
from app.services.knowledge_service import process_file

async def reprocess_latest():
    print("🔄 Re-processing latest knowledge file with improved RAG rules...")

    async with async_session_maker() as session:
        # Get the latest file
        from sqlalchemy import select, desc
        stmt = select(KnowledgeFile).order_by(desc(KnowledgeFile.created_at)).limit(1)
        result = await session.execute(stmt)
        kf = result.scalar_one_or_none()

        if not kf:
            print("❌ No files found to process")
            return

        print(f"📄 File: {kf.original_name}")
        print(f"   Status: {kf.status} → processing")

        # Delete existing chunks
        delete_stmt = delete(KnowledgeChunk).where(KnowledgeChunk.file_id == kf.id)
        await session.execute(delete_stmt)

        # Reset file status
        kf.status = "processing"
        kf.chunk_count = 0
        kf.summary = None
        await session.commit()

        print(f"✅ Cleared {kf.chunk_count} old chunks")

    # Re-process
    await process_file(kf.id)

    # Check results
    async with async_session_maker() as session:
        kf = await session.get(KnowledgeFile, kf.id)
        print(f"✅ Re-processing complete!")
        print(f"   Status: {kf.status}")
        print(f"   Chunks: {kf.chunk_count}")
        print(f"   Summary: {kf.summary}")

if __name__ == "__main__":
    asyncio.run(reprocess_latest())
