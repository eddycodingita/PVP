import urllib.request, json

url = "https://pvp.giustizia.it/ric-496b258c-986a1b71/ric-ms/ricerca/vendite"

headers = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0"
}

body = json.dumps({
    "language": "it",
    "page": 0,
    "size": 1,
    "sortProperty": "dataPubblicazione",
    "sortDirection": "DESC",
    "codTipoLotto": "IMMOBILI",
    "nazione": "Italia"
}).encode("utf-8")

req = urllib.request.Request(url, data=body, headers=headers, method="POST")
r = urllib.request.urlopen(req)
data = json.loads(r.read())
print(json.dumps(data["body"]["content"][0], indent=2, ensure_ascii=False))