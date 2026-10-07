"""Regenerate all platform icons from the shared tower-station master."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

def main():
    image = Image.open(ROOT / "assets/app-icon.png").convert("RGB")
    def png(path, size):
        image.resize((size, size), Image.Resampling.LANCZOS).save(ROOT / path)
    png("ios/HeliostatViewer/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png", 1024)
    image.save(ROOT / "windows/HeliostatViewer/app.ico", sizes=[(n,n) for n in (16,24,32,48,64,128,256)])
    for density, size in {"mdpi":48,"hdpi":72,"xhdpi":96,"xxhdpi":144,"xxxhdpi":192}.items():
        png(f"android/app/src/main/res/mipmap-{density}/ic_launcher.png",size)
    png("android/app/src/main/res/drawable/ic_launcher_art.png",432)

if __name__ == "__main__":
    main()
