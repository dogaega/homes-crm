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
    if r.get("kind") and r["kind"] not in ("apartment", "apartment or house", "property"):
        parts.append(r["kind"])
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
