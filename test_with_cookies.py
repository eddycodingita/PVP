"""
Test dettaglio PVP con autenticazione cookie.
Esegui: python test_with_cookies.py
"""
import urllib.request
import json

# ── COOKIE copiati dal browser ──────────────────────────────────────────────
COOKIE = (
    "cookiesession1=678A3E1038D412448444ECCFBD985EF3; "
    "aac421a12816ca651d4355c6a74c6def=4d653e329972a04ef570e13f92f461bf; "
    "02228cbfdb06bebf566e6cc76e0739dd=e51db4b60b190ac666027b552d42c755; "
    "30d79f53278b8c3b61cc6fd2f84cbd4c=378933f391133b84cb453ddc2e6b3ed2"
)

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Content-Type": "application/json",
    "Cookie": COOKIE,
    "Referer": "https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio=1738450",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

TEST_ID = 1738450

ENDPOINTS = [
    f"https://pvp.giustizia.it/ric-496b258c-986a1b71/ric-ms/annunci/{TEST_ID}",
    f"https://pvp.giustizia.it/ve-3f723b85-986a1b71/ve-ms/annunci/{TEST_ID}",
    f"https://pvp.giustizia.it/ric-496b258c-986a1b71/ric-ms/annunci/{TEST_ID}?language=it",
]

def fetch(url, body=None):
    method = "POST" if body else "GET"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except Exception as e:
        return 0, str(e)

def find_doc_keys(obj, path="", results=None):
    if results is None:
        results = []
    kws = ["doc","alleg","file","pdf","perizia","ordinanza","avvis","relaz","foto","immagin","url","link"]
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if any(w in k.lower() for w in kws):
                preview = str(v)[:300] if not isinstance(v, (dict,list)) else f"[{type(v).__name__} {len(v)} elem]"
                results.append((p, preview))
            find_doc_keys(v, p, results)
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:3]):
            find_doc_keys(item, f"{path}[{i}]", results)
    return results

print("=" * 70)
print("TEST DETTAGLIO PVP CON COOKIE")
print("=" * 70)

success_data = None

for url in ENDPOINTS:
    print(f"\nGET {url}")
    status, data = fetch(url)
    print(f"  Status: {status}")
    if status == 200:
        print("  OK SUCCESSO!")
        success_data = data
        break
    else:
        print(f"  Risposta: {str(data)[:100]}")

if success_data:
    body = success_data.get("body", success_data)

    print("\n" + "=" * 70)
    print("CHIAVI TOP-LEVEL DEL BODY:")
    print("-" * 70)
    if isinstance(body, dict):
        for k in sorted(body.keys()):
            v = body[k]
            if isinstance(v, list):
                print(f"  {k}: [list {len(v)} elem]")
            elif isinstance(v, dict):
                print(f"  {k}: [dict keys={list(v.keys())[:6]}]")
            else:
                print(f"  {k}: {str(v)[:80]}")

    print("\n" + "=" * 70)
    print("CHIAVI RELATIVE A DOCUMENTI:")
    print("-" * 70)
    docs = find_doc_keys(body)
    if docs:
        for path, val in docs:
            print(f"\n  {path}")
            print(f"    -> {val}")
    else:
        print("  Nessuna trovata")

    with open("detail_full.json", "w", encoding="utf-8") as f:
        json.dump(success_data, f, indent=2, ensure_ascii=False)
    print("\nJSON completo salvato in detail_full.json")

else:
    print("\nTutti gli endpoint hanno fallito anche con i cookie.")
    print("I cookie potrebbero essere scaduti — ricopiali dal browser.")

print("\n" + "=" * 70)
