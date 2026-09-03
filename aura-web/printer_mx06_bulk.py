#!/usr/bin/env python3
"""Resumable MX06 bulk voucher printer for Aura.

Prints one 57 mm voucher at a time so Aura can show real per-voucher progress,
pause between vouchers, and resume from the voucher that failed without creating
new Omada vouchers. The printer lock is held for the active batch job so single
and bulk jobs cannot overlap.

Important hardware limitation: the MX06 does not expose a verified paper-out
status to Aura. "Printed" therefore means the BLE driver accepted that voucher.
The Pause button is the safe way to stop between vouchers before changing paper.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WIDTH = 384
LEFT = 8
RIGHT = 8
TOP_FEED = 8
BOTTOM_FEED = 18
ROW_HEIGHT = 226
HEADER_HEIGHT = 34
BODY_HEIGHT = 158
CUT_Y_OFFSET = 214


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--control-file", required=True)
    parser.add_argument("--mac", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--lock-file", required=True)
    parser.add_argument("--render-only", default="")
    return parser.parse_args()


def load_font(size: int, bold: bool = False):
    from PIL import ImageFont

    names = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
    )
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default()


def text_width(draw, text, font):
    box = draw.textbbox((0, 0), str(text), font=font)
    return box[2] - box[0]


def centered_x(draw, text, font, left=0, right=WIDTH):
    available = right - left
    return left + max(0, (available - text_width(draw, text, font)) // 2)


def write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def read_control(path: Path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return str(payload.get("action") or "run").lower()
    except Exception:
        return "run"


def build_qr(code: str, target: int = 118):
    import qrcode
    from PIL import Image

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=1,
    )
    qr.add_data(str(code))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    return img.resize((target, target), resample=Image.Resampling.NEAREST)


def build_ticket(voucher):
    """Render one complete voucher plus its CUT guide."""
    from PIL import Image, ImageDraw

    height = TOP_FEED + ROW_HEIGHT + BOTTOM_FEED
    image = Image.new("L", (WIDTH, height), 255)
    draw = ImageDraw.Draw(image)

    header_font = load_font(18, bold=True)
    code_font = load_font(46, bold=True)
    label_font = load_font(17, bold=False)
    price_font = load_font(27, bold=True)
    cut_font = load_font(11, bold=True)

    y = TOP_FEED
    card_top = y + 4
    card_bottom = y + HEADER_HEIGHT + BODY_HEIGHT + 4
    card_left = LEFT
    card_right = WIDTH - RIGHT

    draw.rectangle((card_left, card_top, card_right - 1, card_bottom), outline=0, width=2)
    draw.rectangle(
        (card_left + 1, card_top + 1, card_right - 2, card_top + HEADER_HEIGHT),
        fill=0,
    )
    title = "AURA VOUCHER WIFI"
    tx = centered_x(draw, title, header_font, card_left, card_right)
    draw.text((tx, card_top + 6), title, fill=255, font=header_font)

    body_top = card_top + HEADER_HEIGHT + 1
    code = str(voucher.get("code") or "").strip()
    validity = str(voucher.get("validity") or "Voucher").strip()
    raw_price = str(voucher.get("price") or "").strip()
    price = f"₱{raw_price}" if raw_price else "—"

    left_x = card_left + 18
    draw.text((left_x, body_top + 21), code, fill=0, font=code_font)
    draw.line((left_x, body_top + 76, left_x + 82, body_top + 76), fill=0, width=2)
    draw.text((left_x, body_top + 88), validity, fill=0, font=label_font)
    draw.text((left_x, body_top + 112), price, fill=0, font=price_font)

    qr_size = 118
    qr = build_qr(code, qr_size)
    qr_x = card_right - qr_size - 15
    qr_y = body_top + 14
    draw.rectangle((qr_x - 3, qr_y - 3, qr_x + qr_size + 2, qr_y + qr_size + 2), outline=0, width=1)
    image.paste(qr, (qr_x, qr_y))

    cut_y = y + CUT_Y_OFFSET
    dash = 12
    gap = 8
    x = 4
    while x < WIDTH - 4:
        draw.line((x, cut_y, min(x + dash, WIDTH - 4), cut_y), fill=0, width=1)
        x += dash + gap
    cut_text = "CUT"
    cw = text_width(draw, cut_text, cut_font)
    cx = (WIDTH - cw) // 2
    draw.rectangle((cx - 7, cut_y - 8, cx + cw + 7, cut_y + 8), fill=255)
    draw.text((cx, cut_y - 7), cut_text, fill=0, font=cut_font)

    return image.convert("1", dither=Image.Dither.NONE)


def wait_if_paused(control_path: Path, status_path: Path, base: dict, completed: int, next_index: int):
    """Pause only between vouchers; return False if the user cancels."""
    announced = False
    while True:
        action = read_control(control_path)
        if action == "cancel":
            write_json(
                status_path,
                {
                    **base,
                    "ok": True,
                    "state": "cancelled",
                    "completed": completed,
                    "next_index": next_index,
                    "message": f"Stopped after voucher {completed}. You can resume from #{next_index}.",
                },
            )
            return False
        if action != "pause":
            if announced:
                write_json(
                    status_path,
                    {
                        **base,
                        "ok": True,
                        "state": "printing",
                        "completed": completed,
                        "current_index": next_index,
                        "next_index": next_index,
                        "message": f"Resuming at voucher {next_index} of {base['quantity']}.",
                    },
                )
            return True
        if not announced:
            write_json(
                status_path,
                {
                    **base,
                    "ok": True,
                    "state": "paused",
                    "completed": completed,
                    "next_index": next_index,
                    "message": (
                        "Paused before the first voucher. Load paper, then tap Resume."
                        if completed < 1
                        else f"Paused after voucher {completed}. Replace paper, then tap Resume."
                    ),
                },
            )
            announced = True
        time.sleep(0.35)


def run_driver(image, driver: Path, mac: str, index: int):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f"aura-voucher-{index:02d}-", suffix=".pbm", delete=False) as tmp:
            temp_path = Path(tmp.name)
        image.save(temp_path, format="PPM")
        command = [
            sys.executable,
            str(driver),
            str(temp_path),
            "-s",
            f"4,{mac}",
            "-0",
            "-q",
            "2",
        ]
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=55,
            check=False,
            cwd=str(driver.parent),
        )
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "Printer driver failed").strip()
            raise RuntimeError(output.splitlines()[-1] if output else "Printer driver failed")
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


def main():
    args = parse_args()
    payload_path = Path(args.payload)
    status_path = Path(args.status_file)
    control_path = Path(args.control_file)
    driver = Path(args.driver)
    lock_path = Path(args.lock_file)

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    vouchers = payload.get("vouchers") or []
    quantity = len(vouchers)
    start_index = int(payload.get("start_index") or 1)
    end_index = int(payload.get("end_index") or quantity)
    mode = str(payload.get("mode") or "full")
    target_count = end_index - start_index + 1
    base = {
        "job_id": payload.get("job_id"),
        "batch_id": payload.get("batch_id"),
        "quantity": quantity,
        "start_index": start_index,
        "end_index": end_index,
        "target_count": target_count,
        "mode": mode,
    }

    if quantity < 1 or quantity > 50:
        raise RuntimeError("Bulk print requires between 1 and 50 vouchers.")
    if start_index < 1 or end_index > quantity or start_index > end_index:
        raise RuntimeError("Invalid bulk print range.")
    if not driver.exists():
        raise RuntimeError(f"MX06 driver not found: {driver}")

    if args.render_only:
        # Preview the first requested voucher only.
        output = Path(args.render_only)
        output.parent.mkdir(parents=True, exist_ok=True)
        build_ticket(vouchers[start_index - 1]).save(output, format="PNG")
        write_json(status_path, {**base, "ok": True, "state": "done", "completed": end_index, "message": "Rendered voucher preview."})
        return 0

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Printer is busy. Another Aura print job is still active.") from exc

        completed = start_index - 1
        for absolute_index in range(start_index, end_index + 1):
            if not wait_if_paused(control_path, status_path, base, completed, absolute_index):
                return 0

            voucher = vouchers[absolute_index - 1]
            code = str(voucher.get("code") or "")
            write_json(
                status_path,
                {
                    **base,
                    "ok": True,
                    "state": "printing",
                    "completed": completed,
                    "current_index": absolute_index,
                    "next_index": absolute_index,
                    "current_code": code,
                    "message": f"Printing voucher {absolute_index} of {quantity}.",
                },
            )

            try:
                run_driver(build_ticket(voucher), driver, args.mac, absolute_index)
            except Exception as exc:
                write_json(
                    status_path,
                    {
                        **base,
                        "ok": False,
                        "state": "error",
                        "completed": completed,
                        "failed_index": absolute_index,
                        "next_index": absolute_index,
                        "current_code": code,
                        "message": f"Stopped at voucher #{absolute_index}: {exc}",
                    },
                )
                return 1

            completed = absolute_index
            write_json(
                status_path,
                {
                    **base,
                    "ok": True,
                    "state": "printing",
                    "completed": completed,
                    "current_index": absolute_index,
                    "next_index": absolute_index + 1 if absolute_index < end_index else None,
                    "current_code": code,
                    "message": f"Printed {completed} of {quantity}." if mode != "reprint" else f"Reprinted voucher #{absolute_index}.",
                },
            )

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    write_json(
        status_path,
        {
            **base,
            "ok": True,
            "state": "done",
            "completed": completed,
            "next_index": completed + 1 if completed < quantity else None,
            "message": (
                f"Done printing vouchers {start_index}–{end_index}."
                if target_count > 1
                else f"Done printing voucher #{start_index}."
            ),
        },
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        try:
            parsed = parse_args()
            status = Path(parsed.status_file)
            payload = {}
            try:
                payload = json.loads(Path(parsed.payload).read_text(encoding="utf-8"))
            except Exception:
                pass
            vouchers = payload.get("vouchers") or []
            start_index = int(payload.get("start_index") or 1)
            write_json(
                status,
                {
                    "ok": False,
                    "state": "error",
                    "job_id": payload.get("job_id"),
                    "batch_id": payload.get("batch_id"),
                    "quantity": len(vouchers),
                    "start_index": start_index,
                    "end_index": int(payload.get("end_index") or len(vouchers) or start_index),
                    "mode": str(payload.get("mode") or "full"),
                    "completed": max(0, start_index - 1),
                    "failed_index": start_index,
                    "next_index": start_index,
                    "message": str(exc),
                },
            )
        except Exception:
            pass
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
