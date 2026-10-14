"""
Agent tracker: for every agent who replied, what the conversation is concretely
about (property, area, rent/sale, status, mandate), what they asked, and which
of Mark's current client requests to send them — same area, same rent/sale,
budget in the same range — with a draft message asking for brochures of what
they have directly with the owner. Plus the client requests grouped by area and
type. Writes outreach/agent_tracker.xlsx. Nothing is sent.

    python -m outreach.agent_tracker            # uses data/_threads_judged.json
"""

from __future__ import annotations

import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402

from outreach.daily import priority  # noqa: E402
from sync import netwait  # noqa: E402,F401
from sync.worker_client import WorkerClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MONACO = re.compile(r"monaco|monte.?carlo|fontvieille|larvotto|carre ?d.?or|la rousse|saint roman|sun tower|mirabeau|fairmont|park palace|"
                    r"millefiori|moneghetti|condamine|port hercule|jardin exotique|perigord|annonciade", re.I)
ALIAS = {"capdantibes": "antibes", "juanlespins": "antibes", "lecannet": "cannes", "supercannes": "cannes", "capferrat": "saintjeancapferrat",
         "stjeancapferrat": "saintjeancapferrat", "saintjean": "saintjeancapferrat", "capmartin": "roquebrunecapmartin",
         "roquebrune": "roquebrunecapmartin", "thoulesurmer": "theoulesurmer"}


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFD", (s or "").lower()).encode("ascii", "ignore").decode())


def key(t: str | None) -> str:
    if t and MONACO.search(t):
        return "monaco"
    n = norm(t)
    return ALIAS.get(n, n)


def price_of(text: str) -> float | None:
    t = text or ""
    m = re.search(r"€\s?(\d[\d.,\s]*\d|\d)\s*(m\b|million|k\b)?", t, re.I) or re.search(r"(\d[\d.,]*)\s*(m€|M€|million|k€)", t)
    if not m:
        return None
    raw = re.sub(r"[\s,]", "", m.group(1))
    try:
        v = float(raw)
    except ValueError:
        return None
    suf = (m.group(2) or "").lower()
    v *= 1e6 if suf.startswith("m") else 1e3 if suf.startswith("k") else 1
    return v * 4.3 if "/week" in t else v


def rline(c: dict) -> str:
    beds = c.get("bedrooms_min")
    kind = {"villa": "villa", "apartment": "apartment"}.get(c.get("kind"), "apartment or house")
    money = f"up to €{int(c['price_max']):,}" + ("/month" if c.get("transaction_type") == "rent" else "")
    return kind + (f", {beds}+ bedrooms" if beds else "") + f", {money}"


def main() -> None:
    reqs = [r for r in WorkerClient(timeout=120).request("GET", "/sync/outreach/requests")["requests"] if r["c"].get("sheet_no")]
    groups: dict = collections.defaultdict(list)
    for r in reqs:
        s, why = priority(r)
        if s < 0 or not r["c"].get("price_max"):
            continue
        for t in r["c"].get("cities") or []:
            groups[(key(t), r["c"].get("transaction_type") or "sale")].append((s, r))
    J = json.load(open(ROOT / "data" / "_threads_judged.json"))
    wb = Workbook(); ws = wb.active; ws.title = "Agents - who said what"
    hdr, fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")
    ws.append(["Agency", "Agent", "Phone / email", "Last reply", "What it is about (property · area · rent/sale · status · mandate)",
               "Agent asks", "Agent offered", "Requests to send them (same area + type + budget)", "Draft message (you send)"])
    n = 0
    for v in sorted(J.values(), key=lambda v: v["last_reply"], reverse=True):
        props = [p for p in (v.get("properties") or []) if not str(p.get("what") or "").lower().startswith("short:")]
        about = "\n".join(f"• {p.get('what')} · {p.get('area')} · {p.get('tx')} · {p.get('status')} · {p.get('mandate')}"
                          + (f"\n  {p['link']}" if p.get("link") else "") for p in props)
        best, kk, area_name = [], None, ""
        for p in props:
            if p.get("tx") not in ("rent", "sale"):
                continue
            k0, pr = key(p.get("area") or p.get("what")), price_of(p.get("what"))
            ok = [r for s, r in sorted(groups.get((k0, p["tx"]), []), key=lambda sr: -sr[0])
                  if not pr or 0.4 * pr <= r["c"]["price_max"] <= 3 * pr]
            if len(ok) > len(best):
                best, kk, area_name = ok, (k0, p["tx"]), ("Monaco" if k0 == "monaco" else p.get("area"))
        lines, seen = [], set()
        for r in best:
            t = rline(r["c"])
            if t not in seen:
                seen.add(t); lines.append((t, r))
        lines = lines[:3]
        send, draft = [], ""
        if lines:
            send = [f"№{r['c'].get('sheet_no')} {t}" for t, r in lines]
            p0 = v.get("person") or ""
            first = p0.split()[0] if p0 and not re.search(r"\d|agence|real|immo|properties|unknown|monaco|&|estate", p0, re.I) else ""
            draft = ((f"Hi {first}, thank you. " if first else "Hello, thank you. ")
                     + f"Also do you have anything directly from the owner {'for rent' if kk[1] == 'rent' else 'for sale'} in {area_name}? "
                     + "My clients are looking for:\n" + "\n".join(f"• {t}" for t, r in lines)
                     + "\nCould you send me the brochures please?\nThank you\nMark")
            n += 1
        ws.append([v["agency"], v.get("person"), v.get("phone") or v.get("email") or "", v["last_reply"][:10], about,
                   v.get("agent_asks") or "", v.get("agent_offers") or "", "\n".join(send), draft])
    for i, wd in enumerate([26, 18, 18, 11, 70, 34, 30, 44, 60]):
        ws.column_dimensions[chr(65 + i)].width = wd
    for row in ws.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for c in ws[1]:
        c.font, c.fill = hdr, fill
    ws.freeze_panes = "C2"
    ws2 = wb.create_sheet("Request groups")
    ws2.append(["Area", "Rent / Sale", "№", "Client", "Request", "Deadline", "Status"])
    for c in ws2[1]:
        c.font, c.fill = hdr, fill
    for (a, tx), g in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        for s, r in sorted(g, key=lambda sr: -sr[0]):
            ws2.append([a, tx, r["c"].get("sheet_no"), r["name"], rline(r["c"]), r["c"].get("deadline") or "", r["c"].get("status") or ""])
    for i, wd in enumerate([22, 10, 5, 34, 44, 11, 10]):
        ws2.column_dimensions[chr(65 + i)].width = wd
    wb.save(ROOT.parent / "outreach" / "agent_tracker.xlsx")
    print("agents", len(J), "with drafts", n)


if __name__ == "__main__":
    main()
