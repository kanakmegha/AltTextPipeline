"""
Launch script for AltTextPipeline Extraction Inspector
------------------------------------------------------
Starts the local development web server.
"""

import sys
import uvicorn

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8000
    host = "127.0.0.1"
    print("=" * 70)
    print("  AltTextPipeline — Extraction Inspector Web Interface")
    print(f"  Access local inspector in your browser: http://{host}:{port}")
    print("=" * 70)
    uvicorn.run("src.web.app:app", host=host, port=port, reload=True)
