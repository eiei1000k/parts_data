from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

UA = "EI01K-CPU-Exporter/1.0 (Wikipedia+Wikidata; personal script)"

WIKI_LANGS = ["ja", "en"]  # 日本語→英語の順で探す
WIKI_API = "https://{lang}.wikipedia.org/w/api.php"
WDQS_ENDPOINT = "https://query.wikidata.org/sparql"

DEFAULT_IN_PATH = Path("cpus") / "INTEL_CPU_LIST.txt"
DEFAULT_OUT_PATH = Path("cpus") / "ALL_INTEL_CPU.txt"

# 周波数の単位(QID) 変換用
UNIT_TO_GHZ = {
    "http://www.wikidata.org/entity/Q3276763": 1.0,  # gigahertz
    "http://www.wikidata.org/entity/Q732707": 1.0 / 1000,  # megahertz
    "http://www.wikidata.org/entity/Q2143992": 1.0 / 1_000_000,  # kilohertz
    "http://www.wikidata.org/entity/Q39369": 1.0 / 1_000_000_000,  # hertz
}


def read_cpu_list(path: Path) -> List[str]:
    text = path.read_text(encoding="utf-8")
    names: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            names.append(s)
    return names

def wikipedia_qid(title: str) -> Optional[Tuple[str, str]]:
    """
    Returns (qid, lang) if found.
    Uses MediaWiki API prop=pageprops to get wikibase_item.
    """
    for lang in WIKI_LANGS:
        url = WIKI_API.format(lang=lang)
        params = {
            "action": "query",
            "format": "json",
            "redirects": 1,
            "prop": "pageprops",
            "ppprop": "wikibase_item",
            "titles": title,
        }
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        data = r.json()
        pages = data.get("query", {}).get("pages", {})
        # pages is a dict keyed by pageid
        for _, p in pages.items():
            if "missing" in p:
                continue
            qid = p.get("pageprops", {}).get("wikibase_item")
            if qid and re.fullmatch(r"Q\d+", qid):
                return (qid, lang)
        time.sleep(0.1)
    return None

def chunked(xs: List[str], n: int) -> List[List[str]]:
    return [xs[i:i+n] for i in range(0, len(xs), n)]

def wdqs_fetch_specs(qids: List[str]) -> Dict[str, Dict[str, object]]:
    """
    For QIDs, fetch cores(P1141), threads(P7443), clock frequencies(P2149).
    """
    out: Dict[str, Dict[str, object]] = {}
    if not qids:
        return out

    values = " ".join([f"wd:{q}" for q in qids])

    sparql = f"""
    SELECT ?item ?itemLabel
           (SAMPLE(?cores) AS ?cores)
           (SAMPLE(?threads) AS ?threads)
           (GROUP_CONCAT(DISTINCT CONCAT(STR(?freqAmount), "|", STR(?freqUnit)); separator=";") AS ?freqs)
    WHERE {{
      VALUES ?item {{ {values} }}

            OPTIONAL {{ ?item wdt:P1141 ?cores. }}
            OPTIONAL {{ ?item wdt:P7443 ?threads. }}

      OPTIONAL {{
        ?item p:P2149 ?st.
        ?st psv:P2149 ?node.
        ?node wikibase:quantityAmount ?freqAmount.
        ?node wikibase:quantityUnit ?freqUnit.
      }}

      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "ja,en". }}
    }}
    GROUP BY ?item ?itemLabel
    """

    r = requests.get(
        WDQS_ENDPOINT,
        params={"query": sparql, "format": "json"},
        headers={"User-Agent": UA, "Accept": "application/sparql-results+json"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    for b in data.get("results", {}).get("bindings", []):
        item_uri = b["item"]["value"]
        qid = item_uri.rsplit("/", 1)[-1]
        label = b.get("itemLabel", {}).get("value", qid)
        cores = b.get("cores", {}).get("value")
        threads = b.get("threads", {}).get("value")
        freqs_raw = b.get("freqs", {}).get("value", "")

        out[qid] = {
            "label": label,
            "cores": int(float(cores)) if cores else None,
            "threads": int(float(threads)) if threads else None,
            "freqs_raw": freqs_raw,
        }

    return out

def parse_freqs_to_str(freqs_raw: str) -> str:
    """
    freqs_raw: '2.7|http://www.wikidata.org/entity/Q3276763;3.9|...'
    -> '2.7–3.9GHz' or '3.6GHz' or '?'.
    """
    if not freqs_raw:
        return "?"

    ghz_vals: List[float] = []
    unknown_parts: List[str] = []

    for part in freqs_raw.split(";"):
        if not part.strip():
            continue
        if "|" not in part:
            unknown_parts.append(part)
            continue
        amount_s, unit = part.split("|", 1)
        try:
            # amount can be like "+3.20"
            amount = float(amount_s.replace("+", ""))
        except ValueError:
            unknown_parts.append(part)
            continue

        mul = UNIT_TO_GHZ.get(unit)
        if mul is None:
            unknown_parts.append(part)
            continue
        ghz_vals.append(amount * mul)

    if ghz_vals:
        ghz_vals = sorted(set(round(x, 3) for x in ghz_vals))
        if len(ghz_vals) == 1:
            x = ghz_vals[0]
            return f"{trim_float(x)}GHz"
        return f"{trim_float(ghz_vals[0])}–{trim_float(ghz_vals[-1])}GHz"

    # 単位が取れない等：最低限、元の文字列を返す
    return freqs_raw

def trim_float(x: float) -> str:
    s = f"{x:.3f}"
    s = s.rstrip("0").rstrip(".")
    return s

def build_lines(cpu_names: List[str]) -> List[str]:
    # 1) WikipediaからQID収集
    name_to_qid: Dict[str, Optional[str]] = {}
    for name in cpu_names:
        hit = wikipedia_qid(name)
        if not hit:
            name_to_qid[name] = None
            continue
        qid, _lang = hit
        name_to_qid[name] = qid

    qids = [q for q in name_to_qid.values() if q]
    qids_unique = sorted(set(qids))

    # 2) WDQSでまとめて引く（200件ずつ）
    specs: Dict[str, Dict[str, object]] = {}
    for batch in chunked(qids_unique, 200):
        specs.update(wdqs_fetch_specs(batch))
        time.sleep(0.2)

    # 3) 出力（形式: "CPU名" "?C?T" "周波数"）
    out_lines: List[str] = []
    for name in cpu_names:
        qid = name_to_qid.get(name)
        if not qid:
            out_lines.append(f"\"{name}\" \"?C?T\" \"?\"")
            continue

        s = specs.get(qid, {})
        cores = s.get("cores")
        threads = s.get("threads")
        freqs_raw = s.get("freqs_raw", "")

        ct = f"{cores if cores is not None else '?'}C{threads if threads is not None else '?'}T"
        freq_str = parse_freqs_to_str(freqs_raw if isinstance(freqs_raw, str) else "")

        out_lines.append(f"\"{name}\" \"{ct}\" \"{freq_str}\"")

    return out_lines


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export Intel CPU specs via Wikipedia+Wikidata")
    p.add_argument(
        "--in",
        dest="in_path",
        default=str(DEFAULT_IN_PATH),
        help="Input CPU name list (one per line). Default: cpus/INTEL_CPU_LIST.txt",
    )
    p.add_argument(
        "--out",
        dest="out_path",
        default=str(DEFAULT_OUT_PATH),
        help="Output file path. Default: cpus/ALL_INTEL_CPU.txt",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    in_path = Path(args.in_path)
    out_path = Path(args.out_path)

    if not in_path.exists():
        raise SystemExit(
            f"Input file not found: {in_path}\n"
            "Create it with Intel CPU names (one per line), then re-run."
        )

    cpu_names = read_cpu_list(in_path)
    lines = build_lines(cpu_names)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"Wrote {len(lines)} lines to: {out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
