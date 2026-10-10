"""PyInstaller entry point (absolute import, unlike `python -m headtrack_pc`).

The built exe is a windowed program (no console) so a double-click opens the window only.
Text modes (`--cli`, `--selftest`, `--version`, `--help`) attach to the console they were
started from, so `HeadTrackPC.exe --cli` in a terminal still prints; a redirected stdout
(`> file`) is kept as is.
"""
import multiprocessing
import sys

TEXT_FLAGS = ("--cli", "--selftest", "--version", "-h", "--help")


def attach_parent_console() -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return  # handles were inherited (redirection) or a console already exists
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            return
        if sys.stdout is None:
            sys.stdout = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
        if sys.stderr is None:
            sys.stderr = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
        if sys.stdin is None:
            sys.stdin = open("CONIN$", "r")
        print()
    except Exception:
        pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if any(a in TEXT_FLAGS for a in sys.argv[1:]):
        attach_parent_console()
    from headtrack_pc.cli import main
    sys.exit(main())
