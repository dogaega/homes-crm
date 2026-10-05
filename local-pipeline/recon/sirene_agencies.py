"""
Every active real-estate agency (NAF 68.31Z) with an establishment in the
Alpes-Maritimes (06) or the Var (83), from the official company register
(recherche-entreprises.api.gouv.fr, open data). Writes data/sirene_agencies.json:
one row per establishment in 06/83 (company, brand/sign, address, commune).

    python recon/sirene_agencies.py
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

import requests

DATA = Path(__file__).resolve().parents[1] / "data"
API = "https://recherche-entreprises.api.gouv.fr/search"


def fetch(dept: str) -> list[dict]:
    out, page = [], 1
    while True:
        for attempt in range(5):
            r = requests.get(API, params={"activite_principale": "68.31Z", "departement": dept, "etat_administratif": "A",
                                          "per_page": 25, "page": page}, timeout=30, headers={"Accept": "application/json"})
            if r.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            r.raise_for_status()
            break
        d = r.json()
        for c in d["results"]:
            for e in c.get("matching_etablissements") or []:
                if not str(e.get("code_postal") or "").startswith(dept) or e.get("etat_administratif") != "A":
                    continue
                out.append({
                    "siren": c["siren"], "company": c.get("nom_complet"), "sign": (e.get("liste_enseignes") or [None])[0] or e.get("nom_commercial"),
                    "siret": e.get("siret"), "address": e.get("adresse"), "postcode": e.get("code_postal"), "commune": e.get("libelle_commune"),
                    "establishments": c.get("nombre_etablissements"), "dept": dept,
                    # 1000 = sole trader (mostly network agents: IAD, Safti…); company forms otherwise.
                    "legal_form": c.get("nature_juridique"), "size": c.get("categorie_entreprise"),
                    "staff": c.get("tranche_effectif_salarie"),
                })
        if page >= d.get("total_pages", 0):
            return out
        page += 1
        time.sleep(0.2)  # the API allows 7 requests/second


def main() -> None:
    rows = fetch("06") + fetch("83")
    (DATA / "sirene_agencies.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(len(rows), "establishments,", len({r["siren"] for r in rows}), "companies")
    print("biggest groups:", Counter(r["company"] for r in rows).most_common(25))
    print("communes:", Counter(r["commune"] for r in rows).most_common(20))


if __name__ == "__main__":
    main()
