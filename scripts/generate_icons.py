"""Render the shared SVG mark into Windows and Android application icons.

Requires the desktop dependencies and Pillow (included in the build environment).
Run from any directory: python scripts/generate_icons.py
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
BRANDING = ROOT / "assets" / "branding"
RES = ROOT / "apps" / "android" / "app" / "src" / "main" / "res"
ANDROID = "http://schemas.android.com/apk/res/android"
ET.register_namespace("android", ANDROID)
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def write_xml(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="    ")
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n")


def vector(paths: list[ET.Element], *, adaptive: bool = False, monochrome: bool = False) -> ET.Element:
    root = ET.Element("vector", {f"{{{ANDROID}}}{key}": value for key, value in {
        "width": "108dp", "height": "108dp", "viewportWidth": "108", "viewportHeight": "108",
    }.items()})
    container = root
    if adaptive:
        # Keep pins and stroke caps inside the circular adaptive-icon safe area.
        container = ET.SubElement(root, "group", {f"{{{ANDROID}}}{key}": value for key, value in {
            "scaleX": "0.85", "scaleY": "0.85", "translateX": "8.1", "translateY": "8.1",
        }.items()})
    for source in paths:
        attrs = {"pathData": source.attrib["d"]}
        fill = source.get("fill", "none")
        attrs["fillColor"] = "#00000000" if fill == "none" or monochrome else fill
        if source.get("stroke"):
            attrs.update(strokeColor="#FFFFFF" if monochrome else source.attrib["stroke"],
                         strokeWidth=source.attrib["stroke-width"], strokeLineCap="round", strokeLineJoin="round")
        ET.SubElement(container, "path", {f"{{{ANDROID}}}{key}": value for key, value in attrs.items()})
    return root


def main() -> None:
    app = QApplication.instance() or QApplication([])
    svg_path = BRANDING / "asic-monitor.svg"
    svg = svg_path.read_bytes()
    renderer = QSvgRenderer(QByteArray(svg))
    if not renderer.isValid():
        raise ValueError("Invalid branding SVG")
    canvas = QImage(1024, 1024, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)
    painter = QPainter(canvas)
    renderer.render(painter, QRectF(0, 0, 1024, 1024))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not canvas.save(buffer, "PNG"):
        raise RuntimeError("Cannot render PNG icon")
    with Image.open(io.BytesIO(bytes(buffer.data()))) as rendered:
        rendered.save(ROOT / "app.ico", format="ICO", sizes=[(size, size) for size in SIZES])
        rendered.resize((512, 512), Image.Resampling.LANCZOS).save(BRANDING / "asic-monitor.png")

    source = ET.fromstring(svg)
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    background = source.find("svg:path", namespace)
    chip = source.find("svg:g", namespace)
    if background is None or chip is None:
        raise ValueError("Expected a background path and chip group")
    paths = list(chip)
    write_xml(RES / "drawable" / "ic_launcher.xml", vector([background, *paths]))
    write_xml(RES / "drawable" / "ic_launcher_foreground.xml", vector(paths, adaptive=True))
    write_xml(RES / "drawable" / "ic_launcher_monochrome.xml", vector(paths, adaptive=True, monochrome=True))

    resources = ET.Element("resources")
    ET.SubElement(resources, "color", {"name": "ic_launcher_background"}).text = background.attrib["fill"]
    write_xml(RES / "values" / "ic_launcher_colors.xml", resources)
    legacy = ET.Element("resources")
    ET.SubElement(legacy, "item", {"type": "mipmap", "name": "ic_launcher"}).text = "@drawable/ic_launcher"
    write_xml(RES / "values" / "ic_launcher_alias.xml", legacy)
    for version in (26, 33):
        adaptive = ET.Element("adaptive-icon")
        for name, drawable in (("background", "@color/ic_launcher_background"),
                               ("foreground", "@drawable/ic_launcher_foreground")):
            ET.SubElement(adaptive, name, {f"{{{ANDROID}}}drawable": drawable})
        if version == 33:
            ET.SubElement(adaptive, "monochrome", {f"{{{ANDROID}}}drawable": "@drawable/ic_launcher_monochrome"})
        write_xml(RES / f"mipmap-anydpi-v{version}" / "ic_launcher.xml", adaptive)
    print(f"Generated Windows ICO ({len(SIZES)} sizes), PNG, and Android legacy/adaptive/monochrome icons.")
    # Keep the Qt application alive until rendering has finished.
    del app


if __name__ == "__main__":
    main()
