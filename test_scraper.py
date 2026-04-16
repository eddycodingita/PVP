import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.env_check import load_dotenv
load_dotenv()

import asyncio, httpx
from db.client import get_supabase

async def test():
    # Step 1: carica tutti gli ID già in DB in memoria (una sola query)
    print("Carico ID esistenti dal DB...")
    sb = get_supabase()
    existing_ids = set()
    offset = 0
    while True:
        res = sb.table("aste").select("pvp_id").range(offset, offset + 999).execute()
        if not res.data:
            break
        for r in res.data:
            existing_ids.add(r["pvp_id"])
        offset += 1000
        if len(res.data) < 1000:
            break
    print(f"ID in DB: {len(existing_ids)}")

    async with httpx.AsyncClient(timeout=30) as client:
        # Step 2: prima chiamata per sapere quante pagine ci sono ORA
        r0 = await client.post(
            "https://pvp.giustizia.it/ric-496b258c-986a1b71/ric-ms/ricerca/vendite",
            json={"language":"it","page":0,"size":50,
                  "codTipoLotto":"IMMOBILI","nazione":"Italia"},
            headers={"Content-Type":"application/json","Accept":"application/json"},
        )
        body0       = r0.json()["body"]
        total_pages = body0["totalPages"]   # fissiamo questo valore
        total_elem  = body0["totalElements"]
        print(f"Totale aste API: {total_elem} | Pagine da scansionare: {total_pages}")

        # Step 3: scorri esattamente total_pages pagine — niente di più
        nuove = []
        for page in range(total_pages):
            try:
                r = await client.post(
                    "https://pvp.giustizia.it/ric-496b258c-986a1b71/ric-ms/ricerca/vendite",
                    json={"language":"it","page":page,"size":50,
                          "codTipoLotto":"IMMOBILI","nazione":"Italia"},
                    headers={"Content-Type":"application/json","Accept":"application/json"},
                )
                content = r.json()["body"]["content"]
                for item in content:
                    if str(item["id"]) not in existing_ids:
                        nuove.append(item)
            except Exception as e:
                print(f"  Errore pagina {page}: {e}")

            if (page + 1) % 100 == 0 or page == total_pages - 1:
                print(f"  Pagina {page+1}/{total_pages} — nuove: {len(nuove)}")

            await asyncio.sleep(0.05)

    print(f"\n✅ Totale nuove aste da importare: {len(nuove)}")

asyncio.run(test())