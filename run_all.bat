@echo off
:loop
python scraper/document_downloader.py --limit 1000 --concurrency 3
echo Run completato. Riprendo tra 30 secondi...
timeout /t 30
goto loop