#!/usr/bin/env python3
"""Indexa o acervo público de provas antigas (newCompleaks, Google Drive) em data/provas.json.

O acervo é mantido por estudantes numa pasta pública do Drive, organizada por período, com uma pasta por
disciplina ("DCC205 - Estruturas de Dados"). Este script só LÊ a listagem pública (sem API key) e guarda nomes,
links e quantidade de arquivos de cada pasta. O site não incorpora nada do Drive: mostra apenas links.

Uso:
  python3 scripts/sync_provas.py                  # pasta padrão
  python3 scripts/sync_provas.py --folder <id>    # outra pasta
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT_FOLDER = "11qr5vHPEckWX-TjPq8f7D6DfMUrAfElW"
OUT = Path(__file__).resolve().parent.parent / "data" / "provas.json"
UA = "Mozilla/5.0 (DCCHub-sync; leitura do acervo publico de provas)"
CODE = re.compile(r"^\s*(?:\((?:vazio)[^)]*\)\s*)?([A-Z]{3}\d{3})\s*[-–]\s*(.+?)\s*$")
MAX_DEPTH = 6


def folder_url(fid: str) -> str:
    return f"https://drive.google.com/drive/folders/{fid}"


def listing(fid: str, retries: int = 3) -> list[dict]:
    """Itens de uma pasta pública: [{name, folder|None, url}]."""
    url = f"https://drive.google.com/embeddedfolderview?id={fid}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
            break
        except Exception:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    items = []
    for m in re.finditer(r'<a href="([^"]+)"[^>]*>.*?<div class="flip-entry-title">(.*?)</div>', page, re.S):
        link, name = html.unescape(m.group(1)), " ".join(html.unescape(m.group(2)).split())
        sub = re.search(r"/folders/([\w-]+)", link)
        items.append({"name": name, "folder": sub.group(1) if sub else None, "url": link})
    time.sleep(0.25)  # gentil com o Drive
    return items


def count_files(fid: str, depth: int = 0) -> int:
    total = 0
    for it in listing(fid):
        if it["folder"]:
            if depth < MAX_DEPTH:
                total += count_files(it["folder"], depth + 1)
        else:
            total += 1
    return total


def clean_title(name: str) -> str:
    name = re.sub(r"^\s*\([^)]*\)\s*", "", name)  # "(vazio) ", "(vazio + 🦕) "
    name = re.sub(r"[\U0001F300-\U0001FAFF]", "", name)
    return " ".join(name.split()).strip(" -–_")


def course_entry(it: dict, log) -> dict:
    m = CODE.match(it["name"])
    code, title = m.group(1), m.group(2)
    title = re.sub(r"^T[OÓ]PICOS\s*-\s*", "", clean_title(title), flags=re.I).strip()
    files = count_files(it["folder"])
    log(f"    {code} {title[:50]:50s} {files:3d} arquivo(s)")
    return {"code": code, "title": title, "url": folder_url(it["folder"]), "files": files,
            "note": "conteúdo no Antigo Compleaks" if "🦕" in it["name"] and not files else None}


def link_courses(data: dict) -> tuple[int, int]:
    """Liga cada pasta à disciplina do catálogo: pelo código (inclui versões antigas: DCC205 -> DCC221) ou,
    para tópicos e códigos genéricos (DCC030…), pelo nome. Só disciplinas em oferta (data/catalog.json)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sync_dcc  # noqa: PLC0415
    catalog = json.loads(sync_dcc.OUT.read_text())
    by_code: dict[str, str] = {}
    for c in catalog["courses"]:
        for code in [c["id"], *c.get("aliases", []), *c["codes"]]:
            if code not in sync_dcc.TOPIC_CODES:
                by_code.setdefault(code, c["id"])
    by_name = {sync_dcc.name_key(c["name"]): c["id"] for c in catalog["courses"]}
    linked = total = 0
    for sec in data["sections"]:
        for e in sec["courses"]:
            total += 1
            e["course"] = (by_name.get(sync_dcc.name_key(e["title"])) if e["code"] in sync_dcc.TOPIC_CODES
                           else by_code.get(e["code"]) or by_name.get(sync_dcc.name_key(e["title"])))
            linked += bool(e["course"])
    return linked, total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", default=ROOT_FOLDER)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--offline", action="store_true", help="não acessa o Drive: só religa as pastas ao catálogo atual")
    args = parser.parse_args()
    log = lambda *a: print(*a, file=sys.stderr)  # noqa: E731
    if args.offline:
        data = json.loads(args.out.read_text())
        linked, total = link_courses(data)
        args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        log(f"✓ {args.out}: {linked}/{total} pastas ligadas a disciplinas do catálogo")
        return 0

    sections, extras = [], []
    for top in listing(args.folder):
        if not top["folder"]:
            extras.append({"title": re.sub(r"\.(pdf|png|jpe?g)$", "", top["name"], flags=re.I), "url": top["url"]})
            continue
        log(f"• {top['name']}")
        items, others = [], []
        for it in listing(top["folder"]):
            if it["folder"] and CODE.match(it["name"]):
                items.append(course_entry(it, log))
            elif it["folder"]:
                files = count_files(it["folder"])
                others.append({"title": clean_title(it["name"]), "url": folder_url(it["folder"]), "files": files})
                log(f"    (pasta) {it['name']}: {files} arquivo(s)")
        sections.append({"title": clean_title(top["name"]), "url": folder_url(top["folder"]), "courses": items, "folders": others})

    data = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": "newCompleaks",
        "url": folder_url(args.folder),
        "sections": sections,
        "extras": extras,
    }
    linked, _ = link_courses(data)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    log(f"  {linked} pastas ligadas a disciplinas do catálogo")
    n = sum(len(s["courses"]) for s in sections)
    filled = sum(1 for s in sections for c in s["courses"] if c["files"])
    log(f"✓ {args.out}: {len(sections)} seções, {n} pastas de disciplina ({filled} com arquivos), "
        f"{sum(c['files'] for s in sections for c in s['courses'])} arquivos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
