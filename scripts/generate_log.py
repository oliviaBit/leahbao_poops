#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
產生鬣寶中藥 log.md。

前提:
- 「一個總結日資料夾」年/月/日/ 內,放約兩週的所有照片 + log.md。
- 每張照片的日期來自 EXIF(DateTimeOriginal);
- 本腳本負責把照片清單套進列表;

近況:讀 Google Sheet「提交日 = 總結日」那列,與上一筆比較,相同欄位自動填「〃」。

本機測試:
    python scripts/generate_log.py --summary 2026-08-11 --days 14 --repo-root . --dry-run
"""

import os, io, csv, sys, argparse, json
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen, Request

# ────────────── 設定 ──────────────
OWNER    = os.environ.get("OWNER", "oliviaBit")
REPO     = os.environ.get("REPO", "leahbao_poops")
BRANCH   = os.environ.get("BRANCH", "main")
SHEET_ID = os.environ.get("SHEET_ID", "12m-ozdcdyN4JvdidQBRCjL2lsi5pqk1sRjpjAUaWMzY")
GID      = os.environ.get("GID", "0")
FALLBACK_DAYS = int(os.environ.get("FALLBACK_DAYS", "14"))  # 第一筆(無上一列)時往前涵蓋天數
DITTO    = "〃"
IMAGE_EXT = {".jpeg", ".jpg", ".png", ".heic", ".heif", ".webp"}

# 近況欄位對應(sheet 欄名 → log.md);改欄名/順序/要不要比對〃,只改這裡
STATUS_LAYOUT = [
    ("口腔黏液",   "口腔水合",       "compare"),
    ("__哈氣__",   None,             "group"),
    ("睡覺時",     "發現哈氣-睡覺時", "compare_sub"),
    ("進食後",     "發現哈氣-進食",   "compare_sub"),
    ("休息時",     "發現哈氣-休息",   "compare_sub"),
    ("食慾",       "食慾 g",         "appetite"),
    ("__體重__", None,             "weight"),
    ("精神",       "精神狀態",       "compare"),
    ("行動",       "外觀行為",       "compare"),
    ("保養品",     "保養品",         "compare"),
    ("艾克痰噴霧", "艾克痰噴霧",     "compare"),
    ("備註",       "備註",           "raw_if_any"),
]


# ────────────── Google Sheet ──────────────
def fetch_sheet_rows():
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        return list(csv.reader(io.StringIO(resp.read().decode("utf-8-sig"))))


def parse_date(s):
    s = (s or "").strip().replace("-", "/").split(" ")[0]
    try:
        y, m, d = (int(x) for x in s.split("/"))
        return date(y, m, d)
    except Exception:
        return None


def find_records(rows):
    hidx = next((i for i, r in enumerate(rows) if any(c.strip() == "提交日" for c in r)), None)
    if hidx is None:
        raise SystemExit("找不到標頭列(沒有『提交日』欄)")
    headers = {c.strip(): j for j, c in enumerate(rows[hidx]) if c.strip()}
    dc = headers["提交日"]
    recs = []
    for i in range(hidx + 1, len(rows)):
        cells = rows[i]
        if dc < len(cells):
            d = parse_date(cells[dc])
            if d:
                recs.append({"row_num": i + 1, "date": d, "cells": cells})
    recs.sort(key=lambda x: x["date"])
    return headers, recs


def cell(rec, headers, name):
    if rec is None:
        return ""
    i = headers.get(name)
    return rec["cells"][i].strip() if (i is not None and i < len(rec["cells"])) else ""


def build_status(headers, recs, summary_date):
    cur = next((r for r in recs if r["date"] == summary_date), None)
    if cur is None:
        raise SystemExit(f"Sheet 找不到提交日 = {summary_date} 的紀錄")
    prev = None
    for r in recs:
        if r["date"] < summary_date:
            prev = r

    def field(name):
        c = cell(cur, headers, name)
        if prev is not None and c != "" and c == cell(prev, headers, name):
            return DITTO
        return c if c != "" else DITTO

    out = ["## 近況", "", f"> 「{DITTO}」:同上次", ""]
    for label, src, mode in STATUS_LAYOUT:
        if mode == "group":
            out.append("- 哈氣(開嘴呼吸):")
        elif mode == "compare_sub":
            out.append(f"  - {label}:{field(src)}")
        elif mode == "weight":
            w = cell(cur, headers, "體重 kg")
            wd = parse_date(cell(cur, headers, "體重日"))
            wd_txt = wd.strftime("%Y/%m/%d") if wd else cell(cur, headers, "體重日")
            out.append(f"- 體重 ({w}kg) {wd_txt}".rstrip())
        elif mode == "appetite":
            c = cell(cur, headers, src)
            same = prev is not None and c != "" and c == cell(prev, headers, src)
            out.append(f"- 食慾:{DITTO}" if same or not c else f"- 食慾:{c}g")
        elif mode == "raw_if_any":
            c = cell(cur, headers, src)
            if c:
                out.append(f"- {label}:{c}")
        else:
            out.append(f"- {label}:{field(src)}")

    link = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid={GID}&range=A{cur['row_num']}"
    out += ["", f"📄 資料來源:[近況記錄 {summary_date.strftime('%m/%d')}]({link})"]
    return "\n".join(out)


# ────────────── EXIF 日期 ──────────────
def exif_date(path):
    """回傳照片拍攝日(date)或 None。優先 DateTimeOriginal。"""
    try:
        from PIL import Image
        try:
            import pillow_heif  # HEIC 支援(有裝才註冊)
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        exif = Image.open(path).getexif()
        raw = ""
        try:
            raw = exif.get_ifd(0x8769).get(0x9003, "")  # ExifIFD → DateTimeOriginal
        except Exception:
            raw = ""
        if not raw:
            raw = exif.get(0x0132, "")                   # 退而求其次:DateTime
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", "ignore")
        if raw:
            return datetime.strptime(raw.strip()[:10], "%Y:%m:%d").date()
    except Exception:
        pass
    return None


# ────────────── 大便照片(單一總結日資料夾 + EXIF 分組) ──────────────
def build_photos(repo_root, summary_date, start_date, annotated):
    folder = repo_root / f"{summary_date.year:04d}" / f"{summary_date.month:02d}" / f"{summary_date.day:02d}"
    by_day, no_exif = {}, []
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.is_file() and p.suffix.lower() in IMAGE_EXT:
                d = exif_date(p)
                (by_day.setdefault(d, []).append(p) if d else no_exif.append(p))

    out = ["## 大便照片", ""]
    span = (summary_date - start_date).days + 1
    for i in range(span):
        d = summary_date - timedelta(days=i)
        label = f"{d.month:02d}/{d.day:02d}"
        imgs = by_day.get(d, [])
        if not imgs:
            out.append(f"- {label} none")
            continue
        parts, any_note = [], False
        for p in imgs:
            is_note = p.name in annotated
            any_note = any_note or is_note
            rel = p.relative_to(repo_root).as_posix()
            url = f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{rel}?raw=true"
            parts.append(f"![{'有註記' if is_note else ''}]({url})")
        out.append((f"- {label} 有註記 " if any_note else f"- {label} ") + "".join(parts))
    out.append("")

    if no_exif:
        out.append("<!-- ⚠️ 以下照片無 EXIF 日期,未排入上方列表,請補日期:")
        for p in no_exif:
            out.append(f"     {p.name}")
        out.append("-->")
        out.append("")
    return "\n".join(out)


# ────────────── 主流程 ──────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default=os.environ.get("SUMMARY_DATE", ""))  # 留空 = Sheet 最新列
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--out", default="")
    ap.add_argument("--annotated", default=os.environ.get("ANNOTATED_JSON", ""))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    annotated = set()
    if args.annotated:
        p = Path(args.annotated)
        annotated = set(json.loads(p.read_text() if p.is_file() else args.annotated))

    rows = fetch_sheet_rows()
    headers, recs = find_records(rows)
    if not recs:
        raise SystemExit("Sheet 沒有任何紀錄")

    # 目標列:指定日,或預設用最新列
    want = parse_date(args.summary)
    target = next((r for r in recs if r["date"] == want), None) if want else recs[-1]
    if target is None:
        raise SystemExit(f"Sheet 找不到提交日 = {want}")
    summary_date = target["date"]

    # 起訖:開始 = 上一列提交日 + 1 天;無上一列時往前 FALLBACK_DAYS-1 天。結束 = 目標日
    prev = next((r for r in reversed(recs) if r["date"] < summary_date), None)
    start_date = (prev["date"] + timedelta(days=1)) if prev else (summary_date - timedelta(days=FALLBACK_DAYS - 1))

    body = "\n\n".join([
        f"# 鬣寶中藥 {summary_date.strftime('%Y/%m/%d')}",
        build_status(headers, recs, summary_date),
        build_photos(repo_root, summary_date, start_date, annotated),
    ]) + "\n"

    out_path = Path(args.out) if args.out else (
        repo_root / f"{summary_date.year:04d}" / f"{summary_date.month:02d}"
        / f"{summary_date.day:02d}" / "log.md")

    if args.dry_run:
        sys.stdout.write(body)
        sys.stderr.write(f"\n[dry-run] 區間 {start_date} ~ {summary_date},會寫到:{out_path}\n")
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body, encoding="utf-8")
        print(f"已寫入 {out_path}(區間 {start_date} ~ {summary_date})")


if __name__ == "__main__":
    main()
