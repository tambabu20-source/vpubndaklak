#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import bz2
import hashlib
from pathlib import Path

EXPECTED_PAYLOAD_LENGTH = 42544
EXPECTED_HASHES = {
    "vbqppl_dashboard_payload_01.txt": "81de38c2413ac0a5ec0ba046579d3a6c385f9d827649825c49fb5d69bdf9e45f",
    "vbqppl_dashboard_payload_02.txt": "9ebbef1ba35248179db8f694f9ed2fd94975a0914653ecf3fdf4df97030f2204",
    "vbqppl_dashboard_payload_03.txt": "fffebe2870add97ac55b4d402babb66a9777e2e93f89c56ac07953e84c937e75",
    "vbqppl_dashboard_payload_04.txt": "30d81f5295b125582d0c0c3a380d75655ccf6ec08d18363033efdf7058525ea0",
    "vbqppl_dashboard_payload_05.txt": "0ba59bc13fe4fa76867e835f5c14a4bc575e18386ae5e8e4eab520bd064a3203",
    "vbqppl_dashboard_payload_06.txt": "c400c863eec5fc9188948828d6a333696e50d9d56fd183a6ce8750a0c25d387f",
    "vbqppl_dashboard_payload_07.txt": "ec94be906da7cb9c7a82af532ce548affac1d244f533f6fa704caaaa33794beb",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Áp dụng gói dashboard VBQPPL đã dựng sẵn.")
    parser.add_argument("--index", default="index.html")
    args = parser.parse_args()

    payload_dir = Path("payload")
    chunks = sorted(payload_dir.glob("vbqppl_dashboard_payload_*.txt"))
    if not chunks:
        raise SystemExit("Không tìm thấy gói dữ liệu dashboard trong thư mục payload.")

    bad = []
    pieces = []
    for chunk in chunks:
        text = chunk.read_text(encoding="ascii").strip()
        digest = hashlib.sha256(text.encode("ascii")).hexdigest()
        expected = EXPECTED_HASHES.get(chunk.name)
        print(f"{chunk.name}: {len(text)} ký tự, sha256={digest}")
        if expected != digest:
            bad.append(f"{chunk.name}: cần {expected}, đang là {digest}")
        pieces.append(text)
    if bad:
        raise SystemExit("Mảnh dữ liệu bị lệch:\n" + "\n".join(bad))

    payload = "".join(pieces)
    if len(payload) != EXPECTED_PAYLOAD_LENGTH:
        raise SystemExit(f"Độ dài gói dữ liệu sai: {len(payload)}/{EXPECTED_PAYLOAD_LENGTH} ký tự.")

    html = bz2.decompress(base64.b85decode(payload))
    Path(args.index).write_bytes(html)
    print(f"Đã cập nhật {args.index} từ {len(chunks)} mảnh dữ liệu: {len(html):,} bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
