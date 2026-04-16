"""
Aspetta il rendering Angular e cerca i link PDF nella pagina dettaglio.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.env_check import load_dotenv
load_dotenv()

import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

TEST_ID  = 1738450
TEST_URL = f"https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio={TEST_ID}"

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx     = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            locale="it-IT",
        )
        page = await ctx.new_page()

        await page.goto(TEST_URL, wait_until="networkidle", timeout=30000)
        
        # Aspetta che la sezione allegati sia visibile
        try:
            await page.wait_for_selector("text=Allegati", timeout=15000)
            print("Sezione Allegati trovata!")
        except:
            print("Sezione Allegati NON trovata entro 15 sec")

        await asyncio.sleep(5)  # aspetta rendering completo

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        print("\n--- TUTTI I LINK <a> CON href ---")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if href and href != "#" and "javascript" not in href:
                print(f"  [{text[:40]}] → {href[:80]}")

        print("\n--- ELEMENTI CON 'scarica' o 'pdf' ---")
        for el in soup.find_all(True):
            text = el.get_text(strip=True).lower()
            attrs = str(el.attrs).lower()
            if ("scarica" in text or ".pdf" in attrs or "download" in attrs) and el.name in ["a", "button", "span"]:
                print(f"  <{el.name}> text='{el.get_text(strip=True)[:50]}' attrs={str(el.attrs)[:100]}")

        print("\n--- SEZIONE ALLEGATI (HTML grezzo) ---")
        allegati_section = soup.find("span", string=lambda t: t and "Allegati" in t)
        if allegati_section:
            parent = allegati_section.find_parent("div", recursive=True)
            if parent:
                print(parent.prettify()[:3000])

        input("\nPremi INVIO per chiudere...")
        await browser.close()

asyncio.run(test())
