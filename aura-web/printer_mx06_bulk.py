#!/usr/bin/env python3
"""One-shot MX06 bulk voucher strip printer.

Renders 10-50 Aura vouchers as one 384 px wide continuous thermal strip,
then sends that strip through the already-verified Cat-Printer MX06 driver.
The process is short-lived and writes progress to a small JSON status file.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
import tempfile
from pathlib import Path

WIDTH = 384
LEFT = 8
RIGHT = 8
TICKET_WIDTH = WIDTH - LEFT - RIGHT
TOP_FEED = 8
BOTTOM_FEED = 28
ROW_HEIGHT = 226
HEADER_HEIGHT = 34
BODY_HEIGHT = 158
CUT_Y_OFFSET = 214


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    parser.add_argument("--status-file", required=True)
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


def write_status(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def build_qr(value: str, target: int = 118):
    """Render QR modules at an integer scale for reliable thermal scanning."""
    import qrcode
    from PIL import Image

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=2,
    )
    qr.add_data(str(value))
    qr.make(fit=True)
    raw = qr.make_image(fill_color="black", back_color="white").convert("L")
    scale = max(1, target // raw.width)
    size = raw.width * scale
    scaled = raw.resize((size, size), resample=Image.Resampling.NEAREST)
    canvas = Image.new("L", (target, target), 255)
    offset = (target - size) // 2
    canvas.paste(scaled, (offset, offset))
    return canvas


def build_strip(vouchers):
    from PIL import Image, ImageDraw

    quantity = len(vouchers)
    height = TOP_FEED + (ROW_HEIGHT * quantity) + BOTTOM_FEED
    image = Image.new("L", (WIDTH, height), 255)
    draw = ImageDraw.Draw(image)

    header_font = load_font(18, bold=True)
    code_font = load_font(46, bold=True)
    label_font = load_font(17, bold=False)
    price_font = load_font(27, bold=True)
    cut_font = load_font(11, bold=True)

    for index, voucher in enumerate(vouchers):
        y = TOP_FEED + (index * ROW_HEIGHT)
        card_top = y + 4
        card_bottom = y + HEADER_HEIGHT + BODY_HEIGHT + 4
        card_left = LEFT
        card_right = WIDTH - RIGHT

        # Ticket border and black header bar, matching the user's original layout.
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

        # Left column: code, duration, price.
        left_x = card_left + 18
        draw.text((left_x, body_top + 21), code, fill=0, font=code_font)
        draw.line((left_x, body_top + 76, left_x + 82, body_top + 76), fill=0, width=2)
        draw.text((left_x, body_top + 88), validity, fill=0, font=label_font)
        draw.text((left_x, body_top + 112), price, fill=0, font=price_font)

        # Right column: QR opens Aura redeem with this voucher pre-filled.
        qr_size = 118
        qr_value = str(voucher.get("qr_url") or code).strip()
        qr = build_qr(qr_value, qr_size)
        qr_x = card_right - qr_size - 15
        qr_y = body_top + 14
        draw.rectangle((qr_x - 3, qr_y - 3, qr_x + qr_size + 2, qr_y + qr_size + 2), outline=0, width=1)
        image.paste(qr, (qr_x, qr_y))

        # Dashed cutting guide after every voucher.
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


def main():
    args = parse_args()
    payload_path = Path(args.payload)
    status_path = Path(args.status_file)
    driver = Path(args.driver)
    lock_path = Path(args.lock_file)

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    vouchers = payload.get("vouchers") or []
    quantity = len(vouchers)
    base = {
        "job_id": payload.get("job_id"),
        "batch_id": payload.get("batch_id"),
        "quantity": quantity,
    }

    if quantity < 1 or quantity > 50:
        raise RuntimeError("Bulk print requires between 1 and 50 vouchers.")
    if not driver.exists():
        raise RuntimeError(f"MX06 driver not found: {driver}")

    write_status(
        status_path,
        {**base, "ok": True, "state": "rendering", "message": f"Preparing {quantity} thermal vouchers."},
    )
    strip = build_strip(vouchers)

    if args.render_only:
        output = Path(args.render_only)
        output.parent.mkdir(parents=True, exist_ok=True)
        strip.save(output, format="PNG")
        write_status(
            status_path,
            {**base, "ok": True, "state": "done", "message": f"Rendered {quantity} vouchers."},
        )
        return 0

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="aura-bulk-", suffix=".pbm", delete=False) as tmp:
            temp_path = Path(tmp.name)
        strip.save(temp_path, format="PPM")

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Printer is busy. Wait for the current print to finish.") from exc

            write_status(
                status_path,
                {
                    **base,
                    "ok": True,
                    "state": "printing",
                    "message": f"Printing {quantity} vouchers on the MX06. Cut on each dashed line.",
                },
            )

            command = [
                sys.executable,
                str(driver),
                str(temp_path),
                "-s",
                f"4,{args.mac}",
                "-0",
                "-q",
                "2",
            ]
            timeout_seconds = max(150, quantity * 12)
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                cwd=str(driver.parent),
            )
            if result.returncode != 0:
                output = (result.stderr or result.stdout or "Printer driver failed").strip()
                raise RuntimeError(output.splitlines()[-1] if output else "Printer driver failed")

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

        write_status(
            status_path,
            {
                **base,
                "ok": True,
                "state": "done",
                "message": f"Done printing {quantity} vouchers.",
            },
        )
        return 0
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    args = None
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
            write_status(
                status,
                {
                    "ok": False,
                    "state": "error",
                    "job_id": payload.get("job_id"),
                    "batch_id": payload.get("batch_id"),
                    "quantity": len(payload.get("vouchers") or []),
                    "message": str(exc),
                },
            )
        except Exception:
            pass
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
