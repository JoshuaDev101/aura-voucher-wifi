#!/usr/bin/env python3
"""One-shot MX06 single voucher printer.

Uses the same compact 384px ticket layout as Aura bulk printing:
- black AURA VOUCHER WIFI header
- large voucher code
- validity + price
- QR redeem URL with voucher code pre-filled
- dashed CUT guide
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urlencode

WIDTH = 384
LEFT = 8
RIGHT = 8
TOP_FEED = 8
BOTTOM_FEED = 28
ROW_HEIGHT = 226
HEADER_HEIGHT = 34
BODY_HEIGHT = 158
CUT_Y_OFFSET = 214


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True)
    parser.add_argument("--validity", required=True)
    parser.add_argument("--price", default="")
    parser.add_argument("--status-url", required=True)
    parser.add_argument("--mac", required=True)
    parser.add_argument("--driver", required=True)
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


def build_redeem_url(status_url: str, code: str) -> str:
    """Convert http://host/status into http://host/redeem?code=XXXXXX."""
    raw = str(status_url or "").strip()
    try:
        parsed = urlsplit(raw)
        if parsed.scheme and parsed.netloc:
            return urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    "/redeem",
                    urlencode({"code": str(code)}),
                    "",
                )
            )
    except Exception:
        pass

    base = raw.rstrip("/")
    if base.endswith("/status"):
        base = base[:-7]
    return f"{base}/redeem?{urlencode({'code': str(code)})}"


def build_qr(value: str, target: int = 118):
    """Render QR modules at integer scale for reliable thermal scanning."""
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


def build_ticket(code: str, validity: str, price_raw: str, status_url: str):
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

    # Same ticket frame/header as bulk.
    draw.rectangle((card_left, card_top, card_right - 1, card_bottom), outline=0, width=2)
    draw.rectangle(
        (card_left + 1, card_top + 1, card_right - 2, card_top + HEADER_HEIGHT),
        fill=0,
    )

    title = "AURA VOUCHER WIFI"
    tx = centered_x(draw, title, header_font, card_left, card_right)
    draw.text((tx, card_top + 6), title, fill=255, font=header_font)

    body_top = card_top + HEADER_HEIGHT + 1

    code = str(code or "").strip()
    validity = str(validity or "Voucher").strip()
    price_raw = str(price_raw or "").strip()
    price = f"₱{price_raw}" if price_raw else "—"

    # Left column.
    left_x = card_left + 18
    draw.text((left_x, body_top + 21), code, fill=0, font=code_font)
    draw.line((left_x, body_top + 76, left_x + 82, body_top + 76), fill=0, width=2)
    draw.text((left_x, body_top + 88), validity, fill=0, font=label_font)
    draw.text((left_x, body_top + 112), price, fill=0, font=price_font)

    # Right column QR: opens /redeem with this voucher pre-filled.
    qr_size = 118
    qr_value = build_redeem_url(status_url, code)
    qr = build_qr(qr_value, qr_size)
    qr_x = card_right - qr_size - 15
    qr_y = body_top + 14
    draw.rectangle(
        (qr_x - 3, qr_y - 3, qr_x + qr_size + 2, qr_y + qr_size + 2),
        outline=0,
        width=1,
    )
    image.paste(qr, (qr_x, qr_y))

    # Same dashed cutting guide as bulk.
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

    return image.convert("1", dither=Image.Dither.NONE), qr_value


def main():
    args = parse_args()
    driver = Path(args.driver)

    if not driver.exists() and not args.render_only:
        raise RuntimeError(f"MX06 driver not found: {driver}")

    ticket, qr_value = build_ticket(
        args.code,
        args.validity,
        args.price,
        args.status_url,
    )

    if args.render_only:
        output = Path(args.render_only)
        output.parent.mkdir(parents=True, exist_ok=True)
        ticket.save(output, format="PNG")
        print(qr_value)
        return 0

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="aura-single-",
            suffix=".pbm",
            delete=False,
        ) as tmp:
            temp_path = Path(tmp.name)

        ticket.save(temp_path, format="PPM")

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

        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
            cwd=str(driver.parent),
        )

        if result.returncode != 0:
            output = (result.stderr or result.stdout or "Printer driver failed").strip()
            raise RuntimeError(output.splitlines()[-1] if output else "Printer driver failed")

        print("Printed successfully")
        return 0

    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
