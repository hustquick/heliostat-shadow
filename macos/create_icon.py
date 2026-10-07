"""Create the macOS iconset for 塔式镜场设计与优化."""

from pathlib import Path
import sys

from PIL import Image


def main() -> None:
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    image = Image.open(Path(__file__).resolve().parents[1] / "assets/app-icon.png").convert("RGBA")

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
