"""Create the macOS iconset for 塔式镜场设计与优化."""

from pathlib import Path
import sys

from PIL import Image, ImageDraw


def main() -> None:
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    size = 1024
    image = Image.new("RGBA", (size, size), "#071820")
    draw = ImageDraw.Draw(image)
    for inset, color in ((60, "#0b2b37"), (92, "#0f4050"), (126, "#12313b")):
        draw.rounded_rectangle((inset, inset, size-inset, size-inset), radius=190, fill=color)
    # Receiver tower and glowing aperture.
    draw.polygon([(470, 690), (554, 690), (532, 320), (492, 320)], fill="#e8f4f5")
    draw.rounded_rectangle((430, 250, 594, 350), radius=28, fill="#f2a541")
    draw.ellipse((468, 274, 556, 362), fill="#ffe1a1")
    # Three staggered heliostat rows.
    mirror = "#53c5dc"
    edge = "#b7edf5"
    for row, y in enumerate((735, 805, 875)):
        count = 5 + row * 2
        width = 92 - row * 8
        gap = 22
        total = count * width + (count - 1) * gap
        start = (size - total) / 2
        for column in range(count):
            x = start + column * (width + gap)
            draw.rounded_rectangle((x, y, x + width, y + 34), radius=8,
                                   fill=mirror, outline=edge, width=4)
    # Incoming and reflected light paths.
    draw.line((180, 190, 430, 290), fill="#ffd166", width=18)
    draw.polygon([(430, 290), (384, 250), (397, 314)], fill="#ffd166")
    draw.line((594, 300, 806, 176), fill="#ff7b54", width=18)
    draw.polygon([(806, 176), (758, 181), (790, 224)], fill="#ff7b54")

    variants = {
        "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
    }
    for name, pixels in variants.items():
        image.resize((pixels, pixels), Image.Resampling.LANCZOS).save(output / name)


if __name__ == "__main__":
    main()
