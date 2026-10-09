"""PyInstaller entry point (absolute import, unlike `python -m headtrack_pc`)."""
import multiprocessing
import sys

from headtrack_pc.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
