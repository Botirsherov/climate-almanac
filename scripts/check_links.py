#!/usr/bin/env python3
"""Reachability checker — the seed of automated monitoring.

For each catalog entry, probes source.canonical_url and reports whether the
declared `status` still matches reality. Read-only.

Two refinements keep the monitor from crying wolf at bot defenses:
  1. Browser-UA retry. A block code (401/403/406/429) triggers one retry with a
     common browser User-Agent — some hosts only sniff the UA.
  2. Blocked != dead. If a host still returns a block code after the retry, the
     source is reported as *blocked / unverifiable* — NOT flagged as an outage.
     Only genuine failures (404, 5xx, connection/timeout) flag an entry.

Uses curl for reliable wall-clock timeouts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog"
DEFAULT_TIMEOUT = 12
UA = "ClimateAlmanac-link-checker/0.1 (+https://climatealmanac.com)"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
BLOCK_CODES = {401, 403, 406, 429}


def _curl(url: str, timeout: float, ua: str) -> tuple[int | None, str]:
    cmd = ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
           "--max-time", str(int(timeout)), "-A", ua, "-L", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5, check=False)
    except subprocess.TimeoutExpired:
        return None, f"timeout>{timeout + 5}s"
    if proc.returncode != 0 and not proc.stdout.strip().isdigit():
        err = (proc.stderr or proc.stdout or "curl failed").strip().splitlines()[-1]
        return None, err[:120]
    raw = proc.stdout.strip()
    if not raw.isdigit():
        return None, raw or "no status code"
    return int(raw), ""


def _probe(url: str, timeout: float) -> tuple[int | None, str]:
    if not shutil.which("curl"):
        raise SystemExit("check_links.py requires curl on PATH")
    code, note = _curl(url, timeout, UA)
    if code in BLOCK_CODES:
        bcode, bnote = _curl(url, timeout, BROWSER_UA)
        if bcode is not None and bcode < 400:
            return bcode, f"ok via browser-UA (almanac-UA got {code})"
        c = bcode if bcode is not None else code
        if c in BLOCK_CODES:
            return c, f"blocked by bot protection ({c}) — cannot auto-verify"
        return c, bnote or f"http {c}"
    return code, note


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SEC",
                    help=f"per-request timeout in seconds (default: {DEFAULT_TIMEOUT})")
    args = ap.parse_args()

    report = []
    problems = 0
    for path in sorted(CATALOG.glob("*.yaml")):
        entry = yaml.safe_load(path.read_text())
        url = entry.get("source", {}).get("canonical_url")
        declared = entry.get("status")
        code, note = _probe(url, args.timeout)
        blocked = code in BLOCK_CODES
        reachable = code is not None and code < 400
        dead = (not reachable) and (not blocked)
        flagged = declared in ("live", "frozen") and dead
        if flagged:
            problems += 1
        report.append({"id": entry.get("id"), "url": url, "declared_status": declared,
                       "http": code, "reachable": reachable, "blocked": blocked,
                       "flagged": flagged, "note": note})

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for r in report:
            mark = "FLAG" if r["flagged"] else ("blok" if r["blocked"] else ("ok  " if r["reachable"] else "warn"))
            print(f"[{mark}] {r['id']:34} status={r['declared_status']:8} http={r['http']}  {r['note']}")
        print(f"\n{problems} entr{'y' if problems == 1 else 'ies'} declared live/frozen but unreachable")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
