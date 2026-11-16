"""Generate fleet relief maps with Python 3.12, NumPy 2.2.6 and Pillow 11.3.

Mapzen Terrain Tiles (Terrarium encoding), accessed 2026-11-15 via the public
AWS Open Data registry: https://registry.opendata.aws/terrain-tiles/
Global SRTM and GMTED2010 elevation data courtesy of the U.S. Geological Survey.
Upstream credits: https://github.com/tilezen/joerd/blob/master/docs/attribution.md
The image is derived by tinting elevation and shading slopes in Web Mercator.
Run this file with `cerro-pasco` for the Peruvian demo or `atacama` for the original map.
"""

import argparse
import hashlib
import io
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple
from urllib.request import urlopen

import numpy as np
from PIL import Image

ZOOM = 13
ASSETS = Path(__file__).resolve().parents[1] / "src/assets"


class Region(NamedTuple):
    columns: range
    rows: range
    heights: tuple[int, ...]
    mosaic_sha256: str
    output: str


REGIONS = {
    "atacama": Region(
        range(2500, 2507), range(4645, 4652), (600, 900, 1150, 1400, 1700, 2100),
        "4b3e471011287c84e35a984ce0bccd20bf6cf1d866d677b9948c18e3c7097ab6", "relieve-flota.webp",
    ),
    "cerro-pasco": Region(
        range(2358, 2363), range(4338, 4343), (3500, 3900, 4100, 4275, 4425, 4600),
        "b84f1f0b6eb33f21e9921ef70892b20472f5381ccc46e64762dee527216058ef", "relieve-cerro-pasco.webp",
    ),
}


def read_tile(xy):
    x, y = xy
    url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{ZOOM}/{x}/{y}.png"
    with urlopen(url, timeout=30) as response:
        with Image.open(io.BytesIO(response.read())) as tile:
            if tile.size != (256, 256):
                raise ValueError(f"Unexpected elevation tile dimensions at {x}/{y}")
            return np.asarray(tile.convert("RGB"))


def build_relief(elevation, region):
    # Use ground spacing at the crop's center latitude when shading slopes.
    center_y = (region.rows.start + region.rows.stop) / 2
    latitude = math.atan(math.sinh(math.pi * (1 - 2 * center_y / 2**ZOOM)))
    spacing = 40_075_016.6856 * math.cos(latitude) / (256 * 2**ZOOM)
    dy, dx = np.gradient(elevation, spacing)
    # Illuminate from the northwest. Image y grows southward.
    shade = np.tanh(4 * (dx + dy) / math.sqrt(2))
    colors = np.array([
        [202, 181, 158], [215, 194, 164], [226, 209, 179],
        [212, 189, 158], [201, 174, 145], [226, 208, 185],
    ])
    rgb = np.stack([np.interp(elevation, region.heights, c) for c in colors.T], axis=2)
    # Thin 50 m contour lines. Adapt width to slope so steep slopes stay legible.
    slope_per_pixel = spacing * np.hypot(dx, dy)
    distance_to_contour = np.abs((elevation + 25) % 50 - 25)
    contour = np.clip(1 - distance_to_contour / np.maximum(0.01, slope_per_pixel), 0, 1)
    rgb += (47 * shade - 17 * contour)[:, :, None]
    h, w = elevation.shape
    y, x = np.ogrid[:h, :w]
    edge = np.minimum(np.minimum(x, w - 1 - x), np.minimum(y, h - 1 - y))
    opacity = np.clip(edge / 50, 0, 1)
    opacity = opacity**2 * (3 - 2 * opacity)
    rgba = np.dstack((np.clip(rgb, 0, 255), 255 * opacity))
    return Image.fromarray(np.rint(rgba).astype(np.uint8))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("region", choices=REGIONS, nargs="?", default="atacama")
    region = REGIONS[parser.parse_args().region]
    positions = [(x, y) for y in region.rows for x in region.columns]
    pixels = np.empty((256 * len(region.rows), 256 * len(region.columns), 3), dtype=np.uint8)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (x, y), tile in zip(positions, pool.map(read_tile, positions), strict=True):
            row, col = 256 * (y - region.rows.start), 256 * (x - region.columns.start)
            pixels[row : row + 256, col : col + 256] = tile
    if hashlib.sha256(pixels).hexdigest() != region.mosaic_sha256:
        raise ValueError("The elevation source has changed. Review it before publishing new terrain.")
    r, g, b = pixels.astype(np.float32).transpose(2, 0, 1)
    elevation = r * 256 + g + b / 256 - 32768
    output = ASSETS / region.output
    output.parent.mkdir(parents=True, exist_ok=True)
    build_relief(elevation, region).save(output, quality=84, method=6)
    print("Wrote", output, "bytes:", output.stat().st_size)


if __name__ == "__main__":
    main()
