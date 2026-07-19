#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

GOOGLE_DOC_ID = "1lM3yY0Z8IQYFF9IN7YyKvqvb4MvNkSfKCNJPN_H-a6o"
GOOGLE_DOC_EXPORT = f"https://docs.google.com/document/d/{GOOGLE_DOC_ID}/export?format=docx"
TOTAL = 365
MIN_SOURCE_ROWS = 150
FALSE_AUTO_PROGRESS = "Đã xử lý/không còn trong danh mục chưa xử lý của nguồn Google Docs"


def norm(value: str) -> str:
    value = (value or "").lower().replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def load_json(source: str, script_id: str) -> list[dict]:
    match = re.search(rf'<script id="{re.escape(script_id)}" type="application/json">(.*?)</script>', source, re.S)
    if not match:
        raise RuntimeError(f"Không tìm thấy {script_id} trong index.html")
    return json.loads(html.unescape(match.group(1)))


def replace_json(source: str, script_id: str, data: list[dict]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return re.sub(
        rf'(<script id="{re.escape(script_id)}" type="application/json">).*?(</script>)',
        rf"\1{payload}\2",
        source,
        flags=re.S,
    )


def download_docx() -> bytes:
    request = urllib.request.Request(GOOGLE_DOC_EXPORT, headers={"User-Agent": "Mozilla/5.0 VBQPPL dashboard updater"})
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read()
    if not data.startswith(b"PK"):
        raise RuntimeError("Google Docs không trả về file DOCX hợp lệ. Có thể link nguồn chưa mở quyền tải xuống cho GitHub Actions.")
    return data


def cell_text(cell: ET.Element, ns: dict[str, str]) -> str:
    parts: list[str] = []
    for node in cell.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag in {"tab", "br"}:
            parts.append(" ")
    return clean("".join(parts))


def parse_docx_rows(data: bytes) -> dict[str, dict]:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    rows: dict[str, dict] = {}
    for table in root.findall(".//w:tbl", ns):
        for row in table.findall("./w:tr", ns):
            cells = [cell_text(cell, ns) for cell in row.findall("./w:tc", ns)]
            cells = [c for c in cells if c]
            if len(cells) < 4:
                continue
            name_index = next((i for i, text in enumerate(cells) if re.search(r"(Nghị quyết|Quyết định|Chỉ thị).{0,80}(NQ-HĐND|QĐ-UBND|CT-UBND)", text, re.I)), -1)
            if name_index < 0:
                continue
            name = cells[name_index]
            following = cells[name_index + 1 :]
            rows[norm(name)] = {
                "name": name,
                "action": following[0] if len(following) > 0 else "",
                "proposedTime": following[1] if len(following) > 1 else "",
                "deadline": following[2] if len(following) > 2 else "",
                "status": following[3] if len(following) > 3 else "",
                "note": following[4] if len(following) > 4 else "",
            }
    return rows


def parse_vn_date(value: str | None) -> datetime | None:
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(value or ""))
    if not match:
        return None
    day, month, year = map(int, match.groups())
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def valid_progress_date(value: str | None) -> datetime | None:
    parsed = parse_vn_date(value)
    if parsed and parsed.date() <= datetime.now().date():
        return parsed
    return None


def format_vn_date(value: datetime) -> str:
    return f"{value.day}/{value.month}/{value.year}"


def latest_date(rows: list[dict], done: list[dict], updates: list[dict], source_rows: dict[str, dict]) -> str:
    dates: list[datetime] = []
    for item in rows + done + updates:
        for key in ("updatedAt", "completedAt"):
            parsed = valid_progress_date(item.get(key))
            if parsed:
                dates.append(parsed)
    # Chỉ đọc ngày trong nội dung tiến độ/ghi chú; không lấy mốc thời hạn tương lai làm ngày cập nhật.
    for item in source_rows.values():
        for key in ("status", "note"):
            for match in re.finditer(r"\d{1,2}/\d{1,2}/\d{4}", str(item.get(key) or "")):
                parsed = valid_progress_date(match.group(0))
                if parsed:
                    dates.append(parsed)
    return format_vn_date(max(dates)) if dates else format_vn_date(datetime.now())


def month_from(value: str) -> int | None:
    match = re.search(r"tháng\s*(\d{1,2})", value or "", re.I)
    if match:
        month = int(match.group(1))
        if 1 <= month <= 12:
            return month
    quarter = re.search(r"quý\s*([ivx]+)", value or "", re.I)
    if quarter:
        return {"i": 3, "ii": 6, "iii": 9, "iv": 12}.get(quarter.group(1).lower())
    return None


def phase_from(status: str, note: str = "") -> str:
    s = norm(f"{status} {note}")
    if not s or "chưa triển khai" in s or s.startswith("chưa"):
        return "Chưa triển khai"
    if "đã xử lý" in s or "đã hoàn thành" in s or "bãi bỏ bởi" in s or "bãi bỏ tại" in s or "thay thế bằng" in s:
        return "Đã hoàn thành"
    if "trình" in s or "thẩm định" in s or "bctđ" in s or "xin ý kiến thành viên" in s:
        return "Đã trình/thẩm định"
    if "lấy ý kiến" in s or "đang" in s or "đăng ký" in s or "soạn thảo" in s or "quy định chi tiết" in s or "công văn" in s or "phân công" in s:
        return "Đang thực hiện"
    return "Khác"


def priority_from(row: dict) -> str:
    if row.get("phase") == "Đã hoàn thành":
        return "Hoàn thành"
    if row.get("phase") == "Đã trình/thẩm định":
        return "Theo dõi ban hành"
    month = row.get("deadlineMonth")
    if month and month <= 6:
        return "Rất gấp"
    if month and month <= 9:
        return "Cao"
    return row.get("priority") or "Theo kế hoạch"


def find_source(row: dict, source_rows: dict[str, dict]) -> dict | None:
    key = norm(row.get("name", ""))
    if key in source_rows:
        return source_rows[key]
    return next((value for source_key, value in source_rows.items() if key and (key in source_key or source_key in key)), None)


def event_key(event: dict) -> tuple[str, str, str]:
    return (norm(event.get("name", "")), event.get("updatedAt", ""), event.get("progress", ""))


def restore_false_auto_completions(rows: list[dict], done: list[dict], updates: list[dict]) -> tuple[list[dict], list[dict], list[dict], int]:
    false_events = {}
    kept_updates = []
    for event in updates:
        if event.get("progress") == FALSE_AUTO_PROGRESS:
            false_events[norm(event.get("name", ""))] = event
        else:
            kept_updates.append(event)
    if not false_events:
        return rows, done, updates, 0

    active_names = {norm(row.get("name", "")) for row in rows}
    restored = 0
    kept_done = []
    for item in done:
        key = norm(item.get("name", ""))
        if item.get("status") == FALSE_AUTO_PROGRESS and key in false_events:
            event = false_events[key]
            restored_item = dict(item)
            old_status = next((change.get("old") for change in event.get("changes", []) if change.get("label") == "Tình trạng"), "")
            restored_item["status"] = old_status or "Chưa có thông tin"
            restored_item["phase"] = event.get("oldPhase") or phase_from(restored_item.get("status", ""), restored_item.get("note", ""))
            restored_item["priority"] = event.get("oldPriority") or priority_from(restored_item)
            restored_item["completed"] = False
            restored_item.pop("completedAt", None)
            restored_item["recentUpdate"] = bool(restored_item.get("updatedAt") and valid_progress_date(restored_item.get("updatedAt")))
            if key not in active_names:
                rows.append(restored_item)
                active_names.add(key)
                restored += 1
        else:
            kept_done.append(item)
    return rows, kept_done, kept_updates, restored


def update_rows(rows: list[dict], done: list[dict], updates: list[dict], source_rows: dict[str, dict], as_of: str) -> tuple[list[dict], list[dict], list[dict], int]:
    existing_events = {event_key(event) for event in updates}
    active: list[dict] = []
    unmatched = 0

    for row in rows:
        src = find_source(row, source_rows)
        if not src:
            unmatched += 1
            active.append(row)
            continue

        old = {k: row.get(k, "") for k in ("action", "proposedTime", "deadline", "status", "note", "phase", "priority")}
        for key in ("action", "proposedTime", "deadline", "status", "note"):
            if src.get(key):
                row[key] = src[key]
        row["deadlineMonth"] = month_from(row.get("deadline", "")) or row.get("deadlineMonth")
        row["phase"] = phase_from(row.get("status", ""), row.get("note", ""))
        row["priority"] = priority_from(row)
        changes = []
        labels = {"action": "Hình thức xử lý", "proposedTime": "Thời gian sở/ngành đề xuất", "deadline": "Thời hạn UBND", "status": "Tình trạng", "note": "Ghi chú"}
        for key, label in labels.items():
            if clean(old.get(key, "")) != clean(row.get(key, "")):
                changes.append({"label": label, "old": old.get(key, ""), "new": row.get(key, "")})
        if changes:
            row["recentUpdate"] = True
            row["updatedAt"] = as_of
            event = {
                "stt": row.get("stt", ""),
                "name": row.get("name", ""),
                "field": row.get("field", ""),
                "agency": row.get("agency", ""),
                "updatedAt": as_of,
                "oldPhase": old.get("phase", ""),
                "newPhase": row.get("phase", ""),
                "oldPriority": old.get("priority", ""),
                "newPriority": row.get("priority", ""),
                "progress": row.get("status", ""),
                "changes": changes,
            }
            if event_key(event) not in existing_events:
                updates.append(event)
                existing_events.add(event_key(event))
        active.append(row)

    for index, row in enumerate(active, start=1):
        row["stt"] = index
    return active, done, updates, unmatched


def date_key(value: str) -> tuple[int, int, int]:
    parsed = valid_progress_date(value)
    return (parsed.year, parsed.month, parsed.day) if parsed else (0, 0, 0)


def refresh_text(source: str, rows: list[dict], done: list[dict], as_of: str) -> str:
    remaining = len(rows)
    processed = TOTAL - remaining
    processed_pct = processed / TOTAL * 100
    remaining_pct = remaining / TOTAL * 100
    counts = Counter(row.get("type", "") for row in rows)
    phases = Counter(row.get("phase", "") for row in rows)
    not_started = phases.get("Chưa triển khai", 0)
    deployed = remaining - not_started
    source = re.sub(r"Nguồn: .*?</span>", "Nguồn: Google Docs nguồn chính; cập nhật tự động hằng ngày lúc 16h30</span>", source, count=1)
    source = re.sub(r"Đang theo dõi: \d+ văn bản", f"Đang theo dõi: {remaining} văn bản", source)
    source = re.sub(r"Hoàn thành xử lý: \d+ văn bản", f"Hoàn thành xử lý: {len(done)} văn bản", source)
    source = re.sub(r"\d+ nghị quyết · \d+ quyết định", f"{counts.get('Nghị quyết', 0)} nghị quyết · {counts.get('Quyết định', 0)} quyết định", source, count=1)
    source = re.sub(r"Tóm tắt tiến độ xử lý đến ngày [^<]+", f"Tóm tắt tiến độ xử lý đến ngày {as_of}", source)
    source = re.sub(r"Đến ngày <strong>[^<]+</strong>", f"Đến ngày <strong>{as_of}</strong>", source)
    source = re.sub(r"tỉnh còn <strong>\d+ văn bản</strong>", f"tỉnh còn <strong>{remaining} văn bản</strong>", source)
    source = re.sub(r"đã xử lý <strong>\d+ văn bản</strong>", f"đã xử lý <strong>{processed} văn bản</strong>", source)
    source = re.sub(r"đạt khoảng <strong>[^<]+</strong>; còn lại <strong>[^<]+</strong>", f"đạt khoảng <strong>{processed_pct:.1f}%</strong>; còn lại <strong>{remaining_pct:.1f}%</strong>", source)
    source = re.sub(r"Dashboard đang ghi nhận <strong>\d+ văn bản</strong> đã có triển khai/cập nhật tiến độ và <strong>\d+ văn bản</strong> chưa triển khai xử lý", f"Dashboard đang ghi nhận <strong>{deployed} văn bản</strong> đã có triển khai/cập nhật tiến độ và <strong>{not_started} văn bản</strong> chưa triển khai xử lý", source)
    source = re.sub(r"<div class=\"summary-stat\"><b>[^<]+</b><span>Đã xử lý \d+/365 văn bản</span></div>", f"<div class=\"summary-stat\"><b>{processed_pct:.1f}%</b><span>Đã xử lý {processed}/{TOTAL} văn bản</span></div>", source)
    source = re.sub(r"<div class=\"summary-stat\"><b>[^<]+</b><span>Còn \d+ văn bản chưa hoàn thành</span></div>", f"<div class=\"summary-stat\"><b>{remaining_pct:.1f}%</b><span>Còn {remaining} văn bản chưa hoàn thành</span></div>", source)
    source = re.sub(r"<div class=\"summary-stat\"><b>\d+</b><span>Văn bản đã triển khai xử lý</span></div>", f"<div class=\"summary-stat\"><b>{deployed}</b><span>Văn bản đã triển khai xử lý</span></div>", source)
    source = re.sub(r"<div class=\"summary-stat\"><b>\d+</b><span>Văn bản chưa triển khai xử lý</span></div>", f"<div class=\"summary-stat\"><b>{not_started}</b><span>Văn bản chưa triển khai xử lý</span></div>", source)
    source = re.sub(r"const recentWindowEnd = parseUpdateDate\('[^']+'\);", f"const recentWindowEnd = parseUpdateDate('{as_of}');", source)
    source = re.sub(r"const latestLabel = '[^']+';", f"const latestLabel = '{as_of}';", source)
    return source


def run(index_path: Path) -> None:
    source = index_path.read_text(encoding="utf-8")
    rows = load_json(source, "dataset")
    done = load_json(source, "completedDataset")
    updates = load_json(source, "updatesDataset")
    source_rows = parse_docx_rows(download_docx())
    if len(source_rows) < MIN_SOURCE_ROWS:
        raise RuntimeError(f"Nguồn Google Docs chỉ đọc được {len(source_rows)} dòng, thấp hơn ngưỡng an toàn {MIN_SOURCE_ROWS}; giữ nguyên dashboard cũ.")
    rows, done, updates, restored = restore_false_auto_completions(rows, done, updates)
    as_of = latest_date(rows, done, updates, source_rows)
    rows, done, updates, unmatched = update_rows(rows, done, updates, source_rows, as_of)
    done.sort(key=lambda row: date_key(row.get("completedAt", "")), reverse=True)
    updates.sort(key=lambda row: (date_key(row.get("updatedAt", "")), 1 if row.get("newPhase") == "Đã hoàn thành" else 0), reverse=True)
    source = replace_json(source, "dataset", rows)
    source = replace_json(source, "completedDataset", done)
    source = replace_json(source, "updatesDataset", updates)
    source = refresh_text(source, rows, done, as_of)
    index_path.write_text(source, encoding="utf-8")
    print(f"Đã rà soát Google Docs: còn {len(rows)} văn bản, hoàn thành {len(done)} văn bản, ngày cập nhật {as_of}.")
    print(f"Khôi phục {restored} văn bản bị đánh dấu hoàn thành nhầm; giữ nguyên {unmatched} văn bản chưa khớp chắc chắn với nguồn.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", default="index.html")
    parser.add_argument("--allow-source-failure", action="store_true")
    args = parser.parse_args()
    try:
        run(Path(args.index))
    except Exception as exc:
        print(f"::warning::Không cập nhật được từ Google Docs: {exc}")
        if args.allow_source_failure:
            print("Giữ nguyên dashboard hiện có và kết thúc thành công để không gửi email lỗi.")
            return 0
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
