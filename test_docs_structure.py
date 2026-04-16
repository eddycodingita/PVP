"""
Test API dettaglio PVP — prova vari endpoint per trovare quello giusto.
"""
import urllib.request
import json

TEST_ID = 1738450  # ID asta da testare

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://pvp.giustizia.it/pvp/it/detail_annuncio.page",
    "Origin": "https://pvp.giustizia.it",
}

# Lista di endpoint da provare
ENDPOINTS = [
    # Endpoint ricerca (quello che funziona)
    f"https://pvp.giustizia.it/ric-496b258c-986a1b71/ric-ms/annunci/{TEST_ID}",
    
    # Endpoint vendite/esperimenti
    f"https://pvp.giustizia.it/ve-3f723b85-986a1b71/ve-ms/annunci/{TEST_ID}",
    
    # Endpoint pubblico detail
    f"https://pvp.giustizia.it/pvp-api/annunci/{TEST_ID}",
    f"https://pvp.giustizia.it/pvp/api/annunci/{TEST_ID}",
    
    # Endpoint con language
    f"https://pvp.giustizia.it/ric-496b258c-986a1b71/ric-ms/annunci/{TEST_ID}?language=it",
    
    # POST endpoint
    "POST:https://pvp.giustizia.it/ric-496b258c-986a1b71/ric-ms/annunci/dettaglio",
]

print("=" * 70)
print("TEST ENDPOINT DETTAGLIO PVP")
print("=" * 70)

for endpoint in ENDPOINTS:
    is_post = endpoint.startswith("POST:")
    url = endpoint.replace("POST:", "") if is_post else endpoint
    
    print(f"\n{'POST' if is_post else 'GET'}: {url}")
    print("-" * 60)
    
    try:
        if is_post:
            data = json.dumps({"id": TEST_ID, "language": "it"}).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
        else:
            req = urllib.request.Request(url, headers=HEADERS)
        
        with urllib.request.urlopen(req, timeout=15) as r:
            status = r.status
            body = r.read()
            print(f"   STATUS: {status} OK")
            
            try:
                data = json.loads(body)
                # Mostra struttura
                if isinstance(data, dict):
                    print(f"   CHIAVI: {list(data.keys())}")
                    
                    # Cerca body/content
                    content = data.get("body", data)
                    if isinstance(content, dict):
                        print(f"   BODY KEYS: {list(content.keys())[:15]}...")
                        
                        # Salva questo per analisi
                        with open(f"detail_{TEST_ID}.json", "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        print(f"   SALVATO: detail_{TEST_ID}.json")
                        
            except json.JSONDecodeError:
                print(f"   BODY (non JSON): {body[:200]}")
                
    except urllib.error.HTTPError as e:
        print(f"   ERRORE: HTTP {e.code} - {e.reason}")
    except Exception as e:
        print(f"   ERRORE: {e}")

# Prova anche a vedere cosa restituisce la pagina HTML
print("\n" + "=" * 70)
print("ANALISI PAGINA HTML DETTAGLIO")
print("=" * 70)

html_url = f"https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio={TEST_ID}"
print(f"\nFetch: {html_url}")

try:
    req = urllib.request.Request(html_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode("utf-8", errors="ignore")
        
        # Cerca pattern di URL documenti nel HTML
        import re
        
        # Pattern comuni per link documenti
        patterns = [
            r'href="([^"]*(?:\.pdf|download|documento|allegat)[^"]*)"',
            r'src="([^"]*(?:\.pdf|\.jpg|\.png|immagini)[^"]*)"',
            r'"url"\s*:\s*"([^"]*)"',
            r'"downloadUrl"\s*:\s*"([^"]*)"',
            r'"fileUrl"\s*:\s*"([^"]*)"',
            r'data-url="([^"]*)"',
        ]
        
        found_urls = set()
        for pattern in patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            found_urls.update(matches)
        
        if found_urls:
            print("\nURL trovati nella pagina:")
            for url in sorted(found_urls)[:20]:
                print(f"   {url}")
        else:
            print("   Nessun URL documento trovato nel HTML")
            
        # Cerca JSON inline nella pagina
        json_pattern = r'<script[^>]*>\s*(?:var|let|const)\s+\w+\s*=\s*(\{[^<]+\})\s*;?\s*</script>'
        json_matches = re.findall(json_pattern, html)
        if json_matches:
            print(f"\nTrovati {len(json_matches)} blocchi JSON inline")
            for i, jm in enumerate(json_matches[:3]):
                print(f"   Blocco {i+1}: {jm[:200]}...")
                
except Exception as e:
    print(f"   ERRORE: {e}")

print("\n" + "=" * 70)
print("FATTO! Incolla l'output qui.")
print("=" * 70)