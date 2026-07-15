#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from pathlib import Path

TOTAL = 365
AS_OF = "15/7/2026"
COMPLETIONS = [
    {
        "match": "Quyết định 07/2023/QĐ-UBND",
        "status": "Đã xử lý; bãi bỏ bởi Quyết định số 63/2026/QĐ-UBND",
        "completedAt": "15/7/2026",
    },
    {
        "match": "Quyết định số 31/2024/QĐ-UBND",
        "status": "Đã xử lý; bãi bỏ bởi Quyết định số 63/2026/QĐ-UBND",
        "completedAt": "15/7/2026",
    },
]


def extract_json(source: str, script_id: str) -> list[dict]:
    pattern = rf'<script id="{re.escape(script_id)}" type="application/json">(.*?)</script>'
    match = re.search(pattern, source, re.S)
    if not match:
        raise SystemExit(f"Không tìm thấy dữ liệu {script_id}")
    return json.loads(html.unescape(match.group(1)))


def replace_json(source: str, script_id: str, payload: list[dict]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False)
    pattern = rf'(<script id="{re.escape(script_id)}" type="application/json">).*?(</script>)'
    return re.sub(pattern, rf'\1{encoded}\2', source, flags=re.S)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def event_from(row: dict, status: str, completed_at: str) -> dict:
    return {
        "stt": row.get("stt", ""),
        "name": row.get("name", ""),
        "field": row.get("field", ""),
        "agency": row.get("agency", ""),
        "updatedAt": completed_at,
        "oldPhase": row.get("phase", "Đang xử lý"),
        "newPhase": "Đã hoàn thành",
        "oldPriority": row.get("priority", "Theo dõi"),
        "newPriority": "Hoàn thành",
        "progress": status,
        "highlight": True,
        "changes": [{"label": "Tình trạng", "old": "Còn trong danh mục chưa xử lý", "new": status}],
    }


def done_from(row: dict, status: str, completed_at: str) -> dict:
    item = dict(row)
    item["status"] = status
    item["phase"] = "Đã hoàn thành"
    item["priority"] = "Hoàn thành"
    item["completedAt"] = completed_at
    item["recentUpdate"] = True
    item["updatedAt"] = completed_at
    return item


def apply_completions(rows: list[dict], done: list[dict], updates: list[dict]) -> tuple[list[dict], list[dict], list[dict], int]:
    added = 0
    remaining: list[dict] = []
    done_keys = {normalize(item.get("name", "")) for item in done}
    update_keys = {(normalize(item.get("name", "")), item.get("updatedAt", ""), item.get("progress", "")) for item in updates}

    for row in rows:
        row_name = normalize(row.get("name", ""))
        correction = next((c for c in COMPLETIONS if normalize(c["match"]) in row_name), None)
        if not correction:
            remaining.append(row)
            continue
        status = correction["status"]
        completed_at = correction["completedAt"]
        if row_name not in done_keys:
            done.append(done_from(row, status, completed_at))
            done_keys.add(row_name)
        ev = event_from(row, status, completed_at)
        ev_key = (normalize(ev["name"]), ev["updatedAt"], ev["progress"])
        if ev_key not in update_keys:
            updates.append(ev)
            update_keys.add(ev_key)
        added += 1

    for index, row in enumerate(remaining, start=1):
        row["stt"] = index
    return remaining, done, updates, added


def date_key(value: str) -> tuple[int, int, int]:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", value or "")
    if not m:
        return (0, 0, 0)
    d, mo, y = map(int, m.groups())
    return (y, mo, d)


def refresh_static_text(source: str, rows: list[dict], done: list[dict]) -> str:
    remaining = len(rows)
    processed = TOTAL - remaining
    processed_pct = processed / TOTAL * 100
    remaining_pct = remaining / TOTAL * 100
    counts = Counter(row.get("type", "") for row in rows)
    phases = Counter(row.get("phase", "") for row in rows)
    not_started = phases.get("Chưa triển khai", 0)
    deployed = remaining - not_started

    source = re.sub(r"Tóm tắt tiến độ xử lý đến ngày [^<]+", f"Tóm tắt tiến độ xử lý đến ngày {AS_OF}", source)
    source = re.sub(r"Đến ngày <strong>[^<]+</strong>", f"Đến ngày <strong>{AS_OF}</strong>", source)
    source = re.sub(r"tỉnh còn <strong>\d+ văn bản</strong>", f"tỉnh còn <strong>{remaining} văn bản</strong>", source)
    source = re.sub(r"đã xử lý <strong>\d+ văn bản</strong>", f"đã xử lý <strong>{processed} văn bản</strong>", source)
    source = re.sub(r"đạt khoảng <strong>[^<]+</strong>; còn lại <strong>[^<]+</strong>", f"đạt khoảng <strong>{processed_pct:.1f}%</strong>; còn lại <strong>{remaining_pct:.1f}%</strong>", source)
    source = re.sub(r"Dashboard đang ghi nhận <strong>\d+ văn bản</strong> đã có triển khai/cập nhật tiến độ và <strong>\d+ văn bản</strong> chưa triển khai xử lý", f"Dashboard đang ghi nhận <strong>{deployed} văn bản</strong> đã có triển khai/cập nhật tiến độ và <strong>{not_started} văn bản</strong> chưa triển khai xử lý", source)
    source = re.sub(r"Đang theo dõi: \d+ văn bản", f"Đang theo dõi: {remaining} văn bản", source)
    source = re.sub(r"Hoàn thành xử lý: \d+ văn bản", f"Hoàn thành xử lý: {len(done)} văn bản", source)
    source = re.sub(r"\d+ nghị quyết · \d+ quyết định", f"{counts.get('Nghị quyết', 0)} nghị quyết · {counts.get('Quyết định', 0)} quyết định", source, count=1)
    source = re.sub(r"<div class=\"summary-stat\"><b>[^<]+</b><span>Đã xử lý \d+/365 văn bản</span></div>", f"<div class=\"summary-stat\"><b>{processed_pct:.1f}%</b><span>Đã xử lý {processed}/{TOTAL} văn bản</span></div>", source)
    source = re.sub(r"<div class=\"summary-stat\"><b>[^<]+</b><span>Còn \d+ văn bản chưa hoàn thành</span></div>", f"<div class=\"summary-stat\"><b>{remaining_pct:.1f}%</b><span>Còn {remaining} văn bản chưa hoàn thành</span></div>", source)
    source = re.sub(r"<div class=\"summary-stat\"><b>\d+</b><span>Văn bản đã triển khai xử lý</span></div>", f"<div class=\"summary-stat\"><b>{deployed}</b><span>Văn bản đã triển khai xử lý</span></div>", source)
    source = re.sub(r"<div class=\"summary-stat\"><b>\d+</b><span>Văn bản chưa triển khai xử lý</span></div>", f"<div class=\"summary-stat\"><b>{not_started}</b><span>Văn bản chưa triển khai xử lý</span></div>", source)
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description="Cập nhật dashboard VBQPPL với các văn bản đã xử lý mới nhất.")
    parser.add_argument("--index", default="index.html")
    args = parser.parse_args()

    path = Path(args.index)
    source = path.read_text(encoding="utf-8")
    rows = extract_json(source, "dataset")
    done = extract_json(source, "completedDataset")
    updates = extract_json(source, "updatesDataset")

    rows, done, updates, added = apply_completions(rows, done, updates)
    done.sort(key=lambda item: date_key(item.get("completedAt", "")), reverse=True)
    updates.sort(key=lambda item: (date_key(item.get("updatedAt", "")), 1 if item.get("newPhase") == "Đã hoàn thành" else 0), reverse=True)

    source = replace_json(source, "dataset", rows)
    source = replace_json(source, "completedDataset", done)
    source = replace_json(source, "updatesDataset", updates)
    source = refresh_static_text(source, rows, done)
    path.write_text(source, encoding="utf-8")
    print(f"Đã rà soát {len(COMPLETIONS)} văn bản hoàn thành; cập nhật mới {added}; còn {len(rows)} văn bản phải xử lý.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
