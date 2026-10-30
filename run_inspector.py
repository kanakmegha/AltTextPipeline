
"""
Launch script for AltTextPipeline Extraction Inspector.

Local development:
- Loads environment variables from the project .env file.
- Uses the existing FastAPI application factory.
- Starts Uvicorn in factory mode.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Project root: directory containing this file.
PROJECT_ROOT = Path(__file__).resolve().parent

# Load local configuration before importing the application.
# Existing shell environment variables take precedence.
if not os.environ.get("VERCEL"):
    load_dotenv(PROJECT_ROOT / ".env", override=False)

import uvicorn


def main():
    port = (
        int(sys.argv[1])
        if len(sys.argv) > 1 and sys.argv[1].isdigit()
        else 8000
    )

    host = "127.0.0.1"

    print("=" * 70)
    print("  AltTextPipeline — Extraction Inspector")
    print(f"  Local URL: http://{host}:{port}")
    print("=" * 70)

    uvicorn.run(
        "src.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=True,
    )


if __name__ == "__main__":
    main()
