"""Headless mode: `python -m headtrack_pc --cli` prints the live state once a second.

Useful for a first check without the window, for the opentrack verification harness, and when
the GUI toolkit is unavailable.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import replace

from . import __version__
from . import profile as prof
from .app import App
from .net.discovery import local_ipv4_addresses
from .profile import OutputSettings, SourceKind


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="headtrack_pc", description="HeadTrack PC: webcam/phone head tracking straight into games")
    p.add_argument("--cli", action="store_true", help="run without the window (prints status)")
    p.add_argument("--source", choices=["webcam", "phone"], help="pose source (default: from profile)")
    p.add_argument("--camera", type=int, help="webcam index")
    p.add_argument("--no-game-output", action="store_true", help="disable the freetrack/TrackIR output")
    p.add_argument("--udp", metavar="HOST:PORT", help="also send 48-byte opentrack packets to HOST:PORT")
    p.add_argument("--profile", help="profile JSON path (default: per-user config dir)")
    p.add_argument("--calibrate-after", type=float, default=2.0, help="CLI: seconds before the automatic centre calibration (0 = never)")
    p.add_argument("--duration", type=float, default=0.0, help="CLI: stop after this many seconds (0 = until Ctrl+C)")
    p.add_argument("--no-steam", action="store_true", help="skip the Steamworks initialisation even if steam_api64.dll is present")
    p.add_argument("--selftest", action="store_true", help="check the packaged libraries, model and DLLs, then exit (0 = all good)")
    p.add_argument("--version", action="version", version=f"HeadTrack PC {__version__}")
    return p


def profile_from_args(args) -> prof.TrackingProfile:
    p = prof.load(args.profile)
    if args.source:
        p = replace(p, source=SourceKind(args.source))
    if args.camera is not None:
        p = replace(p, camera=replace(p.camera, index=args.camera))
    out: OutputSettings = p.output
    if args.no_game_output:
        out = replace(out, freetrack_enabled=False)
    if args.udp:
        host, _, port = args.udp.rpartition(":")
        out = replace(out, udp_enabled=True, udp_host=host or "127.0.0.1", udp_port=int(port))
    return replace(p, output=out)


def run_cli(args) -> int:
    app = App(profile_from_args(args), args.profile)
    app.start()
    print(f"HeadTrack PC {__version__} | PC name: {app.pc_name()} | IPs: {', '.join(local_ipv4_addresses()) or '?'}")
    print(f"Phone port {app.profile.phone.track_port}, discovery port {app.profile.phone.discovery_port}")
    started = time.monotonic()
    calibrated = False
    try:
        while True:
            time.sleep(1.0)
            st = app.engine.state
            el = time.monotonic() - started
            if args.calibrate_after and not calibrated and el >= args.calibrate_after and st.raw is not None:
                app.calibrate()
                calibrated = True
                print("Calibrating centre: hold still and look at the screen...")
            raw = st.raw
            out = st.output
            line = (f"[{el:6.1f}s] src={st.active_source.value if st.active_source else '-':6s} cam={st.webcam_status.value:8s} "
                    f"phone={'fresh' if st.phone_fresh else st.phone_status.value:8s} fps={st.fps:4.0f} "
                    f"raw=({raw.yaw:6.1f},{raw.pitch:6.1f},{raw.roll:6.1f}) " if raw else
                    f"[{el:6.1f}s] src={st.active_source.value if st.active_source else '-':6s} cam={st.webcam_status.value:8s} "
                    f"phone={'fresh' if st.phone_fresh else st.phone_status.value:8s} fps={st.fps:4.0f} raw=(no face)            ")
            line += f"out=({out.yaw:6.1f},{out.pitch:6.1f},{out.roll:6.1f}) {st.tracking.value:9s} cal={st.calibration.value}"
            if st.game_name:
                line += f" game={st.game_name}"
            if st.webcam_error:
                line += f" | {st.webcam_error}"
            if st.output_errors:
                line += f" | {st.output_errors}"
            print(line)
            if args.duration and el >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
    return 0


def setup_logging() -> str:
    """Logs to <config dir>/headtrack.log (and stderr in --cli); returns the log path."""
    import logging
    import logging.handlers
    path = os.path.join(prof.config_dir(), "headtrack.log")
    try:
        os.makedirs(prof.config_dir(), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=512_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
    except OSError:
        pass
    return path


def start_steam(args):
    """Optional Steamworks start-up. Returns (steam, exit_code_or_None)."""
    import logging
    from .steam import Steam
    if args.no_steam:
        return None, None
    s = Steam()
    status = s.start()
    logging.getLogger("headtrack").info("steam: %s", status.message)
    if status.restart_requested:
        return s, 0  # Steam relaunches us through the client
    return s, None


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    if args.selftest:
        from . import selftest
        return selftest.run()
    steam, code = start_steam(args)
    if code is not None:
        return code
    try:
        if args.cli:
            return run_cli(args)
        try:
            from .gui import run_gui
        except ImportError as e:
            print(f"GUI unavailable ({e}); use --cli", file=sys.stderr)
            return 2
        return run_gui(profile_from_args(args), args.profile, steam)
    finally:
        if steam is not None:
            steam.shutdown()
