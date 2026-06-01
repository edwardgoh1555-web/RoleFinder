import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

if not os.environ.get("OPENAI_API_KEY"):
    raise SystemExit("ERROR: OPENAI_API_KEY is not set. Copy .env.example to .env and fill in your key.")

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  RoleFinder running at http://localhost:{port}\n")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
