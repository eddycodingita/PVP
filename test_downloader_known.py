"""
Test downloader su aste note con allegati.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.env_check import load_dotenv
load_dotenv()

import asyncio, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

from db.client import get_supabase
from scraper.document_downloader import DocumentDownloader

async def main():
    sb = get_supabase()

    # Resetta mq di aste note con allegati
    aste_test = ["1738450", "1772706", "1774262"]
    for pvp_id in aste_test:
        sb.table("aste").update({"mq": None}).eq("pvp_id", pvp_id).execute()
        print(f"Reset mq per asta {pvp_id}")

    # Esegui downloader
    d = DocumentDownloader(limit=3, concurrency=1)
    s = await d.run()
    print(f"\nRisultato: {s}")

    # Verifica documenti salvati — usa nome_file non nome
    for pvp_id in aste_test:
        res = sb.table("aste").select("id,comune,mq").eq("pvp_id", pvp_id).single().execute()
        if res.data:
            asta_id = res.data["id"]
            docs = sb.table("documenti").select("nome_file,url_originale,tipo").eq("asta_id", asta_id).execute()
            print(f"\nAsta {pvp_id} | {res.data['comune']} | mq={res.data['mq']}")
            print(f"  Documenti trovati: {len(docs.data or [])}")
            for d in (docs.data or []):
                url = (d.get('url_originale') or '')[:80]
                print(f"  - [{d['tipo']}] {(d['nome_file'] or '')[:50]}")
                print(f"    URL: {url}")

asyncio.run(main())
