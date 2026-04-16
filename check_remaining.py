import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.env_check import load_dotenv
load_dotenv()
from db.client import get_supabase

sb = get_supabase()

anni = [
    ("2026", "2026-01-01", "2027-01-01"),
    ("2025", "2025-01-01", "2026-01-01"),
    ("2024", "2024-01-01", "2025-01-01"),
    ("2023", "2023-01-01", "2024-01-01"),
]

for label, da, a in anni:
    tot   = sb.table("aste").select("id").gte("data_pubblicazione", da).lt("data_pubblicazione", a).limit(1000).execute()
    fatte = sb.table("aste").select("id").not_.is_("mq", "null").gte("data_pubblicazione", da).lt("data_pubblicazione", a).limit(1000).execute()
    print(f"{label}: totale≥{len(tot.data)} | processate≥{len(fatte.data)} | run mancanti≈{max(0, len(tot.data)-len(fatte.data))//100}")