#!/usr/bin/env python3
"""One-shot MX06 voucher receipt printer.

This process is intentionally short-lived: render one 384px receipt, connect,
print, disconnect, exit. The Aura web process does not import Pillow/qrcode.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True)
    parser.add_argument("--validity", required=True)
    parser.add_argument("--price", default="")
    parser.add_argument("--status-url", required=True)
    parser.add_argument("--mac", required=True)
    parser.add_argument("--driver", required=True)
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
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def centered(draw, y, text, font, width=384):
    x = max(0, (width - text_width(draw, text, font)) // 2)
    draw.text((x, y), text, fill=0, font=font)


def build_receipt(code: str, validity: str, price: str, status_url: str):
    try:
        from PIL import Image, ImageDraw
        import qrcode
    except ImportError as exc:
        raise RuntimeError(
            "Printer dependencies missing. Install Pillow and qrcode in the Aura venv."
        ) from exc

    width = 384
    height = 590
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)

    title = load_font(24, bold=True)
    small_bold = load_font(16, bold=True)
    tiny = load_font(13, bold=False)
    code_font = load_font(43, bold=True)
    value_font = load_font(21, bold=True)

    centered(draw, 28, "AURA WIFI VOUCHER", title, width)
    draw.line((38, 66, width - 38, 66), fill=0, width=2)

    centered(draw, 92, "VOUCHER CODE", tiny, width)
    centered(draw, 116, code, code_font, width)

    left_x = 42
    right_x = 224
    draw.text((left_x, 184), "VALIDITY", fill=0, font=tiny)
    draw.text((right_x, 184), "PRICE", fill=0, font=tiny)
    draw.text((left_x, 208), validity.upper(), fill=0, font=value_font)
    price_text = f"₱{price}" if price else "—"
    draw.text((right_x, 208), price_text, fill=0, font=value_font)

    draw.line((38, 258, width - 38, 258), fill=0, width=2)

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=4, border=2)
    qr.add_data(status_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("L")
    qr_img.thumbnail((190, 190))
    qx = (width - qr_img.width) // 2
    image.paste(qr_img, (qx, 286))

    centered(draw, 493, "SCAN FOR AURA STATUS", small_bold, width)
    centered(draw, 522, "Remaining time · voucher status", tiny, width)

    # White feed margin. The tested Cat-Printer driver handles the raster protocol.
    return image.convert("1", dither=Image.Dither.NONE)


def main():
    args = parse_args()
    driver = Path(args.driver)
    if not driver.exists():
        raise RuntimeError(f"MX06 driver not found: {driver}")

    receipt = build_receipt(args.code, args.validity, args.price, args.status_url)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="aura-voucher-", suffix=".pbm", delete=False) as tmp:
            temp_path = Path(tmp.name)
        receipt.save(temp_path, format="PPM")

        # This exact Cat-Printer CLI path/protocol was physically verified on the MX06.
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
            timeout=45,
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
