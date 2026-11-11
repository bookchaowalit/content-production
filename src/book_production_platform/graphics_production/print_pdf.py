"""Press-ready PDF for one rendered graphic: bleed, crop marks, and CMYK through an ICC profile.

The page is written directly (one image, a few vector lines) so the PDF carries the boxes a
printer needs: ``TrimBox`` (the finished size), ``BleedBox`` (trim plus bleed) and ``MediaBox``
(bleed plus room for the crop marks). Bleed is made by mirroring the outer strip of the artwork,
because the SVG is drawn only to its own edge. CMYK needs the printer's ICC profile; nothing is
bundled, so a CMYK request without one fails instead of guessing a press condition. The profile
is embedded as the PDF output intent. The file is not validated against PDF/X, so the manifest
says ``pdfx_claimed: false``.
"""

from __future__ import annotations

import hashlib
import io
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageCms, ImageOps

MAX_BLEED_MM = 10.0
MARK_LENGTH_MM = 5.0
MARK_GAP_MM = 2.0  # from the bleed edge to where a crop mark starts
MARK_WIDTH_PT = 0.25
SLUG_PAD_MM = 2.0
MM_PER_INCH = 25.4


class PrintError(ValueError):
    """A print setting or ICC profile cannot be used; the message is a stable code."""


@dataclass(frozen=True)
class PrintProfile:
    """What the press PDF gets on top of the artwork."""

    bleed_mm: float = 0.0
    crop_marks: bool = False
    cmyk_icc: Path | None = None

    @property
    def is_default(self) -> bool:
        return self.bleed_mm == 0 and not self.crop_marks and self.cmyk_icc is None

    def suffix(self, icc_hash: str | None) -> str:
        """Short tag for file and job names, so different profiles never share an output."""

        parts = []
        if self.bleed_mm:
            parts.append(f"b{self.bleed_mm:g}mm")
        if self.crop_marks:
            parts.append("marks")
        if icc_hash:
            parts.append(f"cmyk{icc_hash[:8]}")
        return "-".join(parts)


@dataclass(frozen=True)
class CmykProfile:
    path: Path
    data: bytes
    description: str
    sha256: str


def check_profile(profile: PrintProfile) -> CmykProfile | None:
    """Validate the settings and load the CMYK ICC profile, if one is asked for."""

    if not 0 <= profile.bleed_mm <= MAX_BLEED_MM:
        raise PrintError(f"bleed_mm must be between 0 and {MAX_BLEED_MM:g}")
    if profile.cmyk_icc is None:
        return None
    try:
        data = Path(profile.cmyk_icc).read_bytes()
        icc = ImageCms.ImageCmsProfile(io.BytesIO(data))
    except (OSError, ImageCms.PyCMSError) as exc:
        raise PrintError("icc_profile_unreadable") from exc
    if icc.profile.xcolor_space.strip() != "CMYK" or icc.profile.device_class.strip() != "prtr":
        raise PrintError("icc_profile_not_cmyk_output")
    description = ImageCms.getProfileDescription(icc).strip() or Path(profile.cmyk_icc).name
    return CmykProfile(Path(profile.cmyk_icc), data, description, hashlib.sha256(data).hexdigest())


def mm_to_px(mm: float, dpi: int) -> int:
    return round(mm / MM_PER_INCH * dpi)


def add_bleed(image: Image.Image, bleed_px: int) -> Image.Image:
    """The image grown by ``bleed_px`` on every side, the new strips mirroring the edge."""

    if bleed_px <= 0:
        return image.copy()
    width, height = image.size
    if bleed_px > min(width, height):
        raise PrintError("bleed_larger_than_artwork")
    grown = Image.new(image.mode, (width + 2 * bleed_px, height + 2 * bleed_px))
    grown.paste(image, (bleed_px, bleed_px))
    b = bleed_px
    top = ImageOps.flip(image.crop((0, 0, width, b)))
    bottom = ImageOps.flip(image.crop((0, height - b, width, height)))
    grown.paste(top, (b, 0))
    grown.paste(bottom, (b, height + b))
    # Columns are mirrored from the already-extended image so the corners are filled too.
    left = ImageOps.mirror(grown.crop((b, 0, 2 * b, height + 2 * b)))
    right = ImageOps.mirror(grown.crop((width, 0, width + b, height + 2 * b)))
    grown.paste(left, (0, 0))
    grown.paste(right, (width + b, 0))
    return grown


def to_cmyk(image: Image.Image, cmyk: CmykProfile) -> Image.Image:
    """sRGB to the press condition: relative colorimetric with black point compensation."""

    try:
        transform = ImageCms.buildTransform(
            ImageCms.createProfile("sRGB"),
            ImageCms.ImageCmsProfile(io.BytesIO(cmyk.data)),
            "RGB",
            "CMYK",
            renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
            flags=ImageCms.Flags.BLACKPOINTCOMPENSATION,
        )
        converted = ImageCms.applyTransform(image.convert("RGB"), transform)
    except ImageCms.PyCMSError as exc:
        raise PrintError("cmyk_conversion_failed") from exc
    if converted is None:  # pragma: no cover - applyTransform returns None only with inPlace=True
        raise PrintError("cmyk_conversion_failed")
    return converted


def _num(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _box(x0: float, y0: float, x1: float, y1: float) -> bytes:
    return f"[{_num(x0)} {_num(y0)} {_num(x1)} {_num(y1)}]".encode()


def _crop_marks(trim: tuple[float, float, float, float], offset: float, length: float) -> list[tuple[float, float, float, float]]:
    """Eight lines, two per corner, on the trim lines and outside the bleed."""

    x0, y0, x1, y1 = trim
    lines = []
    for x, sign_x in ((x0, -1), (x1, 1)):
        for y, sign_y in ((y0, -1), (y1, 1)):
            lines.append((x + sign_x * offset, y, x + sign_x * (offset + length), y))  # horizontal, on the trim line y
            lines.append((x, y + sign_y * offset, x, y + sign_y * (offset + length)))  # vertical, on the trim line x
    return lines


def _write_pdf(objects: list[bytes]) -> bytes:
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {len(objects)} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def _stream(header: bytes, data: bytes) -> bytes:
    return b"<< " + header + f" /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"


def press_pdf(png: bytes, *, dpi: int, profile: PrintProfile, cmyk: CmykProfile | None, title: str) -> tuple[bytes, dict[str, Any]]:
    """The press PDF for a rendered PNG and a record of what it contains (for the export manifest)."""

    with Image.open(io.BytesIO(png)) as source:
        artwork = source.convert("RGB")
    trim_w, trim_h = artwork.size
    bleed_px = mm_to_px(profile.bleed_mm, dpi)
    page = add_bleed(artwork, bleed_px)
    if cmyk is not None:
        page = to_cmyk(page, cmyk)
    pt = 72.0 / dpi
    bleed_pt = bleed_px * pt
    slug_pt = (profile.bleed_mm + MARK_GAP_MM + MARK_LENGTH_MM + SLUG_PAD_MM) / MM_PER_INCH * 72 if profile.crop_marks else bleed_pt
    margin = slug_pt - bleed_pt  # room outside the bleed box
    trim = (margin + bleed_pt, margin + bleed_pt, margin + bleed_pt + trim_w * pt, margin + bleed_pt + trim_h * pt)
    bleed_box = (margin, margin, trim[2] + bleed_pt, trim[3] + bleed_pt)
    media = (0.0, 0.0, bleed_box[2] + margin, bleed_box[3] + margin)

    content = [b"q", f"{_num(page.width * pt)} 0 0 {_num(page.height * pt)} {_num(margin)} {_num(margin)} cm".encode(), b"/Im0 Do", b"Q"]
    if profile.crop_marks:
        offset = bleed_pt + MARK_GAP_MM / MM_PER_INCH * 72
        length = MARK_LENGTH_MM / MM_PER_INCH * 72
        # Registration colour (all inks) in CMYK so the marks print on every plate.
        content += [b"q", f"{_num(MARK_WIDTH_PT)} w".encode(), b"1 1 1 1 K" if cmyk else b"0 0 0 RG"]
        for ax, ay, bx, by in _crop_marks(trim, offset, length):
            content.append(f"{_num(ax)} {_num(ay)} m {_num(bx)} {_num(by)} l S".encode())
        content.append(b"Q")
    content_bytes = zlib.compress(b"\n".join(content), 9)
    color_space = b"/DeviceCMYK" if cmyk else b"/DeviceRGB"
    image_bytes = zlib.compress(page.tobytes(), 6)

    catalog = b"<< /Type /Catalog /Pages 2 0 R"
    if cmyk:
        catalog += b" /OutputIntents [<< /Type /OutputIntent /S /GTS_PDFX /OutputConditionIdentifier (Custom)"
        catalog += b" /Info " + _pdf_text(cmyk.description) + b" /DestOutputProfile 6 0 R >>]"
    catalog += b" >>"
    objects = [
        catalog,
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox " + _box(*media) + b" /BleedBox " + _box(*bleed_box)
        + b" /TrimBox " + _box(*trim) + b" /CropBox " + _box(*media)
        + b" /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
        _stream(b"/Filter /FlateDecode", content_bytes),
        _stream(f"/Type /XObject /Subtype /Image /Width {page.width} /Height {page.height} /BitsPerComponent 8 /ColorSpace ".encode()
                + color_space + b" /Filter /FlateDecode", image_bytes),
    ]
    if cmyk:
        objects.append(_stream(b"/N 4 /Filter /FlateDecode", zlib.compress(cmyk.data, 9)))
    objects.append(b"<< /Producer (book-production graphics print export) /Title " + _pdf_text(title) + b" >>")
    pdf = _write_pdf(objects)

    def mm(points: float) -> float:
        return round(points / 72 * MM_PER_INCH, 2)

    info: dict[str, Any] = {
        "trim_size_mm": [mm(trim[2] - trim[0]), mm(trim[3] - trim[1])],
        "bleed_mm": profile.bleed_mm,
        "bleed_px": bleed_px,
        "bleed_method": "mirror" if bleed_px else None,
        "crop_marks": profile.crop_marks,
        "media_size_mm": [mm(media[2]), mm(media[3])],
        "color": "cmyk" if cmyk else "rgb",
        "icc_profile": {"description": cmyk.description, "sha256": cmyk.sha256, "rendering_intent": "relative_colorimetric+bpc"}
        if cmyk else None,
        "pdfx_claimed": False,
    }
    return pdf, info


def _pdf_text(text: str) -> bytes:
    """A PDF string: plain ASCII in parentheses, anything else as UTF-16BE hex."""

    if text.isascii() and text.isprintable():
        return b"(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("ascii") + b")"
    return b"<FEFF" + text.encode("utf-16-be").hex().upper().encode("ascii") + b">"


__all__ = ["MAX_BLEED_MM", "CmykProfile", "PrintError", "PrintProfile", "add_bleed", "check_profile", "mm_to_px", "press_pdf", "to_cmyk"]
