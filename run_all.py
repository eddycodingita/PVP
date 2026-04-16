import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.env_check import load_dotenv
load_dotenv()

import asyncio, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

from scraper.document_downloader import DocumentDownloader

async def main():
    run = 0
    while True:
        run += 1
        print(f"\n=== RUN {run} ===")
        d = DocumentDownloader(limit=1000, concurrency=3)
        s = await d.run()
        if s["processed"] == 0:
            print("Nessuna asta da processare. Fine!")
            break
        print(f"Run {run}: {s}")

asyncio.run(main())