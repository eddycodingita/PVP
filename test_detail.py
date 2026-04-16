import urllib.request, json

id_annuncio = 4561359

# Endpoint dettaglio visto nelle chiamate XHR
url = f"https://pvp.giustizia.it/ric-496b258c-986a1b71/ric-ms/annunci/{id_annuncio}"

headers = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://pvp.giustizia.it/pvp/it/detail_annuncio.page",
}

try:
    req = urllib.request.Request(url, headers=headers)
    r = urllib.request.urlopen(req)
    data = json.loads(r.read())
    print(json.dumps(data, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Errore: {e}")
    
    # Proviamo URL alternativo
    url2 = f"https://pvp.giustizia.it/ve-3f723b85-986a1b71/ve-ms/annunci/{id_annuncio}"
    print(f"\nProvo: {url2}")
    try:
        req2 = urllib.request.Request(url2, headers=headers)
        r2 = urllib.request.urlopen(req2)
        data2 = json.loads(r2.read())
        print(json.dumps(data2, indent=2, ensure_ascii=False))
    except Exception as e2:
        print(f"Errore: {e2}")