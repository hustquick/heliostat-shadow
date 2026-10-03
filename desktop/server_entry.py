"""PyInstaller entry point shared by the macOS and Windows desktop apps."""

from scripts.serve_viewer import main


if __name__ == "__main__":
    main()
