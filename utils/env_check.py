"""
#11 — Validazione variabili d'ambiente + test di connettività reale.

Controlla non solo che le variabili esistano, ma che funzionino:
  - Supabase: esegue una query di test
  - Anthropic: chiama l'API con 1 token
  - Telegram: verifica il bot token (opzionale)

Uso:
    from utils.env_check import load_dotenv, check
    load_dotenv()
    check()  # esce con sys.exit(1) se qualcosa non va

Standalone (diagnostica):
    python utils/env_check.py
    python utils/env_check.py --quick   # solo check variabili, no connettività
"""
import logging
import os
import sys
import time

log = logging.getLogger("env")

REQUIRED: dict[str, str] = {
    "SUPABASE_URL":         "URL del progetto Supabase (es: https://xxx.supabase.co)",
    "SUPABASE_SERVICE_KEY": "Service role key (Supabase → Settings → API → service_role)",
    "ANTHROPIC_API_KEY":    "API key Anthropic (console.anthropic.com → API Keys)",
}

OPTIONAL: dict[str, str] = {
    "OPENAI_API_KEY":    "Necessaria per gli embedding semantici (ricerca testuale)",
    "TELEGRAM_BOT_TOKEN":"Necessaria per alert via Telegram",
    "RESEND_API_KEY":    "Necessaria per alert via email (resend.com)",
    "ALERT_FROM_EMAIL":  "Email mittente per gli alert",
}


# ── Check principale ──────────────────────────────────────────────────
def check(exit_on_error: bool = True, test_connectivity: bool = True) -> bool:
    """
    Verifica variabili e connettività.
    Ritorna True se tutto ok, False altrimenti (o esce se exit_on_error=True).
    """
    ok = True

    # 1. Variabili obbligatorie
    missing = [k for k, _ in REQUIRED.items() if not os.environ.get(k, "").strip()]
    if missing:
        _err_header("VARIABILI D'AMBIENTE MANCANTI")
        for var in missing:
            log.error(f"  ❌ {var}")
            log.error(f"     {REQUIRED[var]}")
        log.error("")
        log.error("Configura le variabili in .env o nei GitHub Secrets")
        log.error("=" * 60)
        if exit_on_error:
            sys.exit(1)
        return False

    # 2. Formato variabili
    ok = ok and _check_formats()

    # 3. Connettività (salva se --quick o test_connectivity=False)
    if test_connectivity:
        ok = ok and _test_supabase()
        ok = ok and _test_anthropic()
        _test_telegram()    # warning, non bloccante

    if ok:
        log.info("✓ Ambiente configurato correttamente")
    else:
        if exit_on_error:
            sys.exit(1)

    return ok


# ── Verifica formati ──────────────────────────────────────────────────
def _check_formats() -> bool:
    ok = True

    url = os.environ.get("SUPABASE_URL", "")
    if not url.startswith("https://") or ".supabase.co" not in url:
        log.warning(f"  ⚠ SUPABASE_URL sembra non valido: {url!r}")
        log.warning("    Formato atteso: https://xxxx.supabase.co")
        ok = False

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key and not key.startswith("sk-ant-"):
        log.warning(f"  ⚠ ANTHROPIC_API_KEY non sembra valida (prefisso atteso: sk-ant-)")
        ok = False

    sb_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if sb_key and not sb_key.startswith("eyJ") and not sb_key.startswith("sb_secret_"):
        log.warning("  ⚠ SUPABASE_SERVICE_KEY non sembra un JWT valido")
        ok = False

    return ok


# ── Test connettività Supabase ─────────────────────────────────────────
def _test_supabase() -> bool:
    log.info("  Testo connessione Supabase...")
    try:
        from supabase import create_client
        t0 = time.time()
        sb = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
        # Query leggera: conta le righe della tabella aste (anche 0 va bene)
        res = sb.table("aste").select("id").limit(1).execute()
        ms = int((time.time() - t0) * 1000)
        log.info(f"  ✓ Supabase OK ({ms}ms)")
        return True
    except Exception as e:
        err = str(e)
        log.error(f"  ❌ Supabase FALLITO: {err}")
        if "invalid api key" in err.lower():
            log.error("    → SUPABASE_SERVICE_KEY non valida")
        elif "not found" in err.lower() or "relation" in err.lower():
            log.error("    → Tabella 'aste' non trovata: eseguire le migrations prima")
        elif "connection" in err.lower():
            log.error("    → Impossibile raggiungere Supabase: verificare SUPABASE_URL")
        return False


# ── Test connettività Anthropic ───────────────────────────────────────
def _test_anthropic() -> bool:
    log.info("  Testo connessione Anthropic...")
    try:
        import anthropic
        t0 = time.time()
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",  # modello più economico per il test
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        ms = int((time.time() - t0) * 1000)
        log.info(f"  ✓ Anthropic OK ({ms}ms)")
        return True
    except Exception as e:
        err = str(e)
        log.error(f"  ❌ Anthropic FALLITO: {err}")
        if "authentication" in err.lower() or "invalid" in err.lower():
            log.error("    → ANTHROPIC_API_KEY non valida o scaduta")
        elif "connection" in err.lower():
            log.error("    → Impossibile raggiungere api.anthropic.com")
        return False


# ── Test Telegram (warning only) ──────────────────────────────────────
def _test_telegram():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return  # opzionale, nessun warning

    log.info("  Testo bot Telegram...")
    try:
        import httpx
        r = httpx.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=5,
        )
        if r.status_code == 200 and r.json().get("ok"):
            name = r.json()["result"].get("username", "?")
            log.info(f"  ✓ Telegram bot OK (@{name})")
        else:
            log.warning(f"  ⚠ Telegram bot token non valido: {r.text[:100]}")
    except Exception as e:
        log.warning(f"  ⚠ Telegram test fallito: {e}")


# ── .env loader ───────────────────────────────────────────────────────
def load_dotenv(path: str = ".env"):
    """
    Carica variabili da un file .env.
    Non sovrascrive variabili già presenti nell'ambiente (sicuro in CI).
    """
    if not os.path.exists(path):
        return
    loaded = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
                loaded += 1
    if loaded:
        log.debug(f"  .env caricato: {loaded} variabili da {path}")


def _err_header(msg: str):
    log.error("=" * 60)
    log.error(f"ERRORE: {msg}")
    log.error("=" * 60)


# ── Standalone ────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    load_dotenv()

    quick = "--quick" in sys.argv
    if quick:
        log.info("Modalità quick (solo variabili, no connettività)\n")

    log.info("PVP Monitor — Diagnostica ambiente")
    log.info("=" * 60)

    # Mostra variabili presenti/mancanti
    log.info("\nVariabili obbligatorie:")
    for var, desc in REQUIRED.items():
        val = os.environ.get(var, "")
        if val:
            masked = val[:8] + "…" + val[-4:] if len(val) > 12 else "***"
            log.info(f"  ✓ {var}: {masked}")
        else:
            log.info(f"  ✗ {var}: NON CONFIGURATA")

    log.info("\nVariabili opzionali:")
    for var, desc in OPTIONAL.items():
        val = os.environ.get(var, "")
        stato = "configurata" if val else f"mancante — {desc}"
        log.info(f"  {'✓' if val else '–'} {var}: {stato}")

    log.info("")
    check(exit_on_error=False, test_connectivity=not quick)
