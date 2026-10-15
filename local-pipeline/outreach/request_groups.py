"""
Lay out client requests for an agent under headings by area and rent/purchase
(Mark, 15.10), e.g.

    MONACO – RENT
    • 1 bedroom, furnished, ready to view now, up to €20,000/month
    • 2–3 bedrooms, up to €25,000/month
    MONACO – PURCHASE
    • 2 bedrooms, up to €3,500,000

Each request stays its own line (budgets are not merged). Never contains
client names.
"""

from __future__ import annotations

from collections import OrderedDict


def request_line(r: dict) -> str:
    """r: kind, beds, notes (list[str]), budget, unit ('' | '/month' | '/week')."""
    parts = []
    parts.append(r["kind"] if r.get("kind") and r["kind"] != "property" else "apartment or house")
    beds = str(r.get("beds") or "")
    if beds:
        parts.append(f"{beds} bedroom{'' if beds == '1' else 's'}")
    parts += [n for n in r.get("notes") or [] if n]
    parts.append(f"up to €{int(r['budget']):,}{r.get('unit') or ''}" if r.get("budget") else "budget open")
    return "• " + ", ".join(parts)


def from_criteria(c: dict) -> dict:
    """CRM request criteria (saved_searches) → the dict group_lines takes."""
    rent = c.get("transaction_type") == "rent"
    beds = c.get("bedrooms_min")
    if beds and c.get("bedrooms_max") and c["bedrooms_max"] != beds:
        beds = f"{beds}–{c['bedrooms_max']}"
    return {"tx": "rent" if rent else "sale", "area": " / ".join(c.get("cities") or []) or "the Riviera",
            "kind": {"villa": "villa", "apartment": "apartment"}.get(c.get("kind"), "apartment"), "beds": beds,
            "budget": c.get("price_max"), "unit": "/month" if rent else "", "notes": []}


def group_lines(reqs: list[dict]) -> list[str]:
    """reqs: dicts with tx ('rent'|'sale'), area, kind, beds, budget, unit, notes. Returns heading + bullet lines."""
    groups: OrderedDict = OrderedDict()
    for r in sorted(reqs, key=lambda r: (r["area"], r["tx"] != "rent", -(r.get("budget") or 0))):
        groups.setdefault((r["area"], r["tx"]), []).append(r)
    out = []
    for (area, tx), rs in groups.items():
        out.append(f"{area.upper()} – {'RENT' if tx == 'rent' else 'PURCHASE'}")
        seen = []
        for r in rs:
            line = request_line(r)
            if line not in seen:
                seen.append(line)
        out += seen
    return out


if __name__ == "__main__":
    demo = [
        {"tx": "sale", "area": "Monaco", "kind": "apartment", "beds": "1", "budget": 2_000_000},
        {"tx": "sale", "area": "Monaco", "kind": "apartment", "beds": "1", "budget": 2_500_000, "notes": ["Carré d'Or only"]},
        {"tx": "sale", "area": "Monaco", "kind": "apartment", "beds": "2", "budget": 3_500_000},
        {"tx": "rent", "area": "Monaco", "kind": "apartment", "beds": "2–3", "budget": 25_000, "unit": "/month"},
        {"tx": "rent", "area": "Monaco", "kind": "apartment", "budget": 25_000, "unit": "/month", "notes": ["Larvotto"]},
        {"tx": "rent", "area": "Monaco", "kind": "apartment", "beds": "1", "budget": 20_000, "unit": "/month", "notes": ["furnished", "ready to view now"]},
    ]
    print("\n".join(group_lines(demo)))


SHEET = "10nkjBwS9ZsWz2QUOiWsPZV2CV3zPwEms56V8P9DT6UI"
AREA_ALIAS = {"Cap d'Antibes": ["Antibes"], "Граница с Monaco": ["Monaco", "Beausoleil"], "Лазурный берег": ["*"]}


def _paused() -> set:
    import json
    from pathlib import Path
    try:
        return {k.lstrip("0") for k in json.loads((Path(__file__).resolve().parents[1] / "data" / "request_priority.json").read_text()).get("skip", {})}
    except (OSError, ValueError):
        return set()


def team_requests() -> list[dict]:
    """Active requests from the team sheet's «Клиенты» tab (statuses Подбор / Новый, with a budget). Internal fields id/no/name
    are for Mark's sheets only — never put them in a message."""
    import csv
    import io
    import re
    import urllib.parse

    import requests
    url = f"https://docs.google.com/spreadsheets/d/{SHEET}/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote('Клиенты')}"
    rows = list(csv.DictReader(io.StringIO(requests.get(url, timeout=60).content.decode("utf-8"))))
    out = []
    for x in rows:
        if not x.get("Статус", "").startswith(("Подбор", "Новый")):
            continue
        if (x.get("№ запроса") or "").lstrip("0") in _paused():
            continue
        rent = x.get("Сделка") == "Аренда"
        wish = x.get("Пожелания") or ""
        try:
            budget = float(re.sub(r"[^\d.]", "", x.get("Бюджет") or "")) if x.get("Бюджет") else None
        except ValueError:
            budget = None
        unit = "/month" if rent else ""
        m = re.search(r"до\s*([\d\s]+)\s*€\s*в\s*неделю", wish)
        if not budget and m:
            budget, unit = float(m.group(1).replace(" ", "")), "/week"
        city = x.get("Город") or ""
        towns = AREA_ALIAS.get(city, ["Cannes", "Menton", "Beaulieu-sur-Mer"] if city == "Не указан" and "Cannes" in wish else [city] if city and city != "Не указан" else [])
        if not budget or not towns:
            continue
        notes = []
        low = wish.lower()
        if "мебел" in low and "неизвест" not in low:
            notes.append("furnished")
        if "закрыт" in low and "паркинг" in low:
            notes.append("closed residence with parking")
        if "carré" in low or "carre" in low:
            notes.append("Carré d'Or only")
        if "larvotto" in low:
            notes.append("Larvotto")
        if "первая линия" in low:
            notes.append("front line on the sea")
        if "belle" in low:
            notes.append("Belle Époque style")
        if "готов смотреть" in low:
            notes.append("ready to view now")
        if "офф-маркет" in low:
            notes.append("off-market only")
        kind = {"Вилла": "villa", "Апартаменты": "apartment"}.get(x.get("Тип"), "property")
        out.append({"id": x["ID"], "no": x.get("№ запроса"), "name": x.get("Клиент"), "tx": "rent" if rent else "sale",
                    "towns": towns, "area": " / ".join(t for t in towns if t != "*") or "the Riviera, near the sea", "kind": kind,
                    "beds": (x.get("Спальни") or "").replace("–", "–") or None, "budget": budget, "unit": unit, "notes": notes,
                    "urgent": bool(re.search(r"Срочно|Приоритет", (x.get("Приоритет") or "") + wish)) or "ready to view now" in notes})
    return _apply_overrides(out, rows)


def _apply_overrides(out: list[dict], rows: list[dict]) -> list[dict]:
    """Merge details from Artem's app (data/request_overrides.json) — e.g. a client the sheet has without a budget."""
    import json
    from pathlib import Path
    try:
        ov = json.loads((Path(__file__).resolve().parents[1] / "data" / "request_overrides.json").read_text())
    except (OSError, ValueError):
        return out
    res = [r for r in out if not (ov.get(r["id"]) or {}).get("replace")]
    for r in res:
        o = ov.get(r["id"]) or {}
        for k, v in o.items():
            r[k] = v
    for x in rows:
        o = ov.get(x.get("ID")) or {}
        if o.get("replace") and x.get("Статус", "").startswith(("Подбор", "Новый")):
            for rr in o["replace"]:
                res.append({"id": x["ID"], "no": x.get("№ запроса"), "name": x.get("Клиент"), **rr})
    return res


def for_specialty(reqs: list[dict], areas: list[dict], limit_per_type: int = 3) -> list[dict]:
    """Requests that fit an agency's specialty: same rent/sale, a town they list in, budget within their price range.
    areas: [{city, tx, n, mn, mx}] from their listings."""
    import re
    import unicodedata
    norm = lambda s: re.sub(r"[^a-z]", "", unicodedata.normalize("NFD", (s or "").lower()).encode("ascii", "ignore").decode())
    areas = [a for a in areas if a.get("n", 0) >= 2 and (a.get("mx") or 0) > 1000]
    monaco_only = bool(areas) and all(norm(a["city"]) == "monaco" for a in areas)
    picked = {"rent": [], "sale": []}
    for r in sorted(reqs, key=lambda r: (not r["urgent"], -(r["budget"] or 0))):
        if r["towns"] == ["*"] and monaco_only:
            continue
        b = (r["budget"] or 0) * (4.3 if r["unit"] == "/week" else 1)
        if not b:  # no budget (e.g. rent in one specific building): match on town + type only
            b = None
        ok = any(a["tx"] == r["tx"] and ("*" in r["towns"] or norm(a["city"]) in {norm(t) for t in r["towns"]})
                 and (b is None or (a.get("mn") or 0) * 0.7 <= b <= a["mx"] * 2) for a in areas)
        if ok and len(picked[r["tx"]]) < limit_per_type and request_line(r) not in {request_line(p) for p in picked[r["tx"]]}:
            picked[r["tx"]].append(r)
    return picked["rent"] + picked["sale"]
