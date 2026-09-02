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
    """Render a compact 57 mm / 384 px voucher receipt.

    Keep paper use low: only the Aura title, voucher code, validity, price,
    and a small status QR are printed. No footer paragraphs or large gaps.
    """
    try:
        from PIL import Image, ImageDraw
        import qrcode
    except ImportError as exc:
        raise RuntimeError(
            "Printer dependencies missing. Install Pillow and qrcode in the Aura venv."
        ) from exc

    width = 384
    height = 372
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)

    title = load_font(22, bold=True)
    label = load_font(12, bold=True)
    code_font = load_font(40, bold=True)
    value_font = load_font(19, bold=True)

    # Header — tight but still readable on 57 mm paper.
    centered(draw, 14, "AURA WIFI VOUCHER", title, width)
    draw.line((30, 48, width - 30, 48), fill=0, width=2)

    # Voucher code is the visual focus.
    centered(draw, 62, code, code_font, width)

    # Validity + price share one compact row.
    left_center = 110
    right_center = 274
    validity_text = validity.upper()
    price_text = f"₱{price}" if price else "—"

    lw = text_width(draw, "VALIDITY", label)
    pw = text_width(draw, "PRICE", label)
    draw.text((left_center - lw // 2, 122), "VALIDITY", fill=0, font=label)
    draw.text((right_center - pw // 2, 122), "PRICE", fill=0, font=label)

    vw = text_width(draw, validity_text, value_font)
    rw = text_width(draw, price_text, value_font)
    draw.text((left_center - vw // 2, 143), validity_text, fill=0, font=value_font)
    draw.text((right_center - rw // 2, 143), price_text, fill=0, font=value_font)

    draw.line((30, 180, width - 30, 180), fill=0, width=1)

    # Small QR keeps the receipt useful without wasting paper.
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=3,
        border=1,
    )
    qr.add_data(status_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("L")
    qr_img.thumbnail((146, 146))
    qx = (width - qr_img.width) // 2
    image.paste(qr_img, (qx, 194))

    # Small bottom feed area only; no marketing/footer copy.
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
