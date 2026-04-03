#!/usr/bin/env python
"""Start the Shan-AI FastAPI server with Telegram bot polling."""

import asyncio
import sys
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    sys.path.insert(0, '/c/Users/livya/Desktop/SHAN-AI')

    from app.main import app
    import uvicorn

    logger.info("=" * 70)
    logger.info("Starting Shan-AI Decision Intelligence Platform")
    logger.info("=" * 70)
    logger.info("FastAPI: http://0.0.0.0:8000")
    logger.info("API Docs: http://0.0.0.0:8000/docs")
    logger.info("Telegram Bot: Polling mode")
    logger.info("=" * 70)
    logger.info("")

    try:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            log_level="info"
        )
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
