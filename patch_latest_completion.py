#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import bz2
from pathlib import Path

EXPECTED_PAYLOAD_LENGTH = 42544


def main() -> int:
    parser = argparse.ArgumentParser(description="Áp dụng gói dashboard VBQPPL đã dựng sẵn.")
    parser.add_argument("--index", default="index.html")
    args = parser.parse_args()

    payload_dir = Path("payload")
    chunks = sorted(payload_dir.glob("vbqppl_dashboard_payload_*.txt"))
    if not chunks:
        raise SystemExit("Không tìm thấy gói dữ liệu dashboard trong thư mục payload.")

    payload = "".join(chunk.read_text(encoding="ascii").strip() for chunk in chunks)
    if len(payload) < EXPECTED_PAYLOAD_LENGTH:
        raise SystemExit(f"Gói dữ liệu chưa đủ: {len(payload)}/{EXPECTED_PAYLOAD_LENGTH} ký tự.")
    payload = payload[:EXPECTED_PAYLOAD_LENGTH]

    html = bz2.decompress(base64.b85decode(payload))
    Path(args.index).write_bytes(html)
    print(f"Đã cập nhật {args.index} từ {len(chunks)} mảnh dữ liệu: {len(html):,} bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
