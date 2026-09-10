from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
import sys
import threading
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from aiohttp import web

from .config import Settings, load_production_settings, load_settings
from .credentials import harden_windows_acl
from .paths import AppPaths, PathMigrationError, PathResolutionError
from .platform_security import (
    PlatformSecurityError,
    activate_private_umask,
    ensure_macos_directory,
    validate_macos_tree,
)
from .single_instance import SingleInstance, existing_instance_is_alive


PRODUCT_VERSION = "1.0.0"


def resource_root() -> Path:
    executable = Path(sys.executable).resolve()
    if (
        sys.platform == "darwin"
        and executable.parent.name == "MacOS"
        and executable.parent.parent.name == "Contents"
    ):
        resources = executable.parent.parent / "Resources"
        if resources.is_dir():
            return resources.resolve()
    compiled = globals().get("__compiled__")
    if compiled is not None and getattr(compiled, "standalone", False):
        # In Nuitka standalone mode sys.executable is the final EXE inside the
        # distribution. ``__compiled__.containing_dir`` is one level higher in
        # Nuitka 4.2 and therefore cannot locate adjacent static/config data.
        return Path(sys.executable).resolve().parent
    containing_dir = getattr(compiled, "containing_dir", None)
    if containing_dir:
        return Path(containing_dir).resolve()
    return Path(__file__).resolve().parent.parent


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="极速下单 - 本地券商API单票交易终端")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    return parser.parse_args(argv)


def configure_production_logging(logs_dir: Path) -> None:
    from app import SdkSecretFilter

    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        logs_dir / "fast_order.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(SdkSecretFilter())
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    console.addFilter(SdkSecretFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler, console], force=True)
    logging.getLogger("aiohttp.access").disabled = True


def prepare_writable_paths(paths: AppPaths, *, legacy_root: Path) -> None:
    try:
        paths.ensure_directories()
        if sys.platform != "darwin":
            paths.migrate_legacy(legacy_root)
        else:
            validate_macos_tree(paths.data_root)
    except (OSError, PathMigrationError, PlatformSecurityError) as exc:
        raise RuntimeError(f"用户数据目录初始化失败: {exc}") from exc
    for path in (paths.data_root, paths.runtime_root, paths.broker_tokens, paths.logs):
        if path.exists() and not harden_windows_acl(path):
            raise RuntimeError(f"无法保护用户数据目录: {path.name}")


def _install_stop_handlers(
    loop: asyncio.AbstractEventLoop,
    stop_event: asyncio.Event,
    cleanup_complete: threading.Event,
) -> Any:
    def request_stop(*_args: object) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, request_stop)
            except (OSError, ValueError):
                pass

    if sys.platform != "win32":
        return None
    import ctypes

    handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    @handler_type
    def console_handler(event: int) -> bool:
        request_stop()
        # Windows terminates a console process after close/logoff/shutdown
        # handlers return, so keep that handler alive while aiohttp cleans up.
        if event in {2, 5, 6}:
            cleanup_complete.wait(timeout=12)
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel32.SetConsoleCtrlHandler(console_handler, True):
        return None
    return console_handler


async def serve(settings: Settings, *, no_browser: bool) -> int:
    # Import after paths/logging are initialized so SDK initialization cannot
    # create files in the installation/current working directory.
    from app import FastOrderApplication

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        listener.bind(("127.0.0.1", settings.port))
        listener.listen(128)
        listener.setblocking(False)
    except OSError as exc:
        listener.close()
        raise RuntimeError(
            f"端口 127.0.0.1:{settings.port} 已被其他程序占用，极速下单未启动"
        ) from exc

    application = FastOrderApplication(settings, static_root=settings.app_paths.static_dir)
    if sys.platform == "darwin":
        try:
            validate_macos_tree(settings.data_root)
        except PlatformSecurityError as exc:
            await application.service.close()
            listener.close()
            raise RuntimeError(f"用户数据权限校验失败: {exc}") from exc
    runner = web.AppRunner(application.app, access_log=None)
    try:
        await runner.setup()
        site = web.SockSite(runner, listener)
        await site.start()
    except BaseException:
        listener.close()
        await runner.cleanup()
        raise

    url = f"http://127.0.0.1:{settings.port}/"
    mode = "Sandbox 模拟" if settings.sandbox else "LIVE 实盘"
    print(f"极速下单 {PRODUCT_VERSION} 已启动: {url}")
    print(f"当前模式: {mode}；按 Ctrl+C 安全退出。")
    if not no_browser:
        webbrowser.open(url)

    stop_event = asyncio.Event()
    cleanup_complete = threading.Event()
    handler_ref = _install_stop_handlers(asyncio.get_running_loop(), stop_event, cleanup_complete)
    try:
        await stop_event.wait()
    finally:
        # Keep the ctypes callback alive until cleanup has completed.
        _ = handler_ref
        await runner.cleanup()
        cleanup_complete.set()
    return 0


def _run(settings: Settings, paths: AppPaths, *, no_browser: bool, legacy_root: Path) -> None:
    url = f"http://127.0.0.1:{settings.port}/"
    try:
        ensure_macos_directory(paths.data_root)
    except PlatformSecurityError as exc:
        raise RuntimeError(f"用户数据目录初始化失败: {exc}") from exc
    lock_path = paths.data_root / ".fastorder.lock" if sys.platform == "darwin" else None
    with SingleInstance.acquire(lock_path) as instance:
        if instance.already_running:
            if existing_instance_is_alive(settings.port):
                if not no_browser:
                    webbrowser.open(url)
                print("极速下单已在运行，已打开现有页面。")
                return
            raise RuntimeError("检测到极速下单单实例锁，但现有实例健康检查失败")
        prepare_writable_paths(paths, legacy_root=legacy_root)
        configure_production_logging(paths.logs)
        asyncio.run(serve(settings, no_browser=no_browser))


def development_main(source_root: Path, argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    activate_private_umask()
    try:
        settings = load_settings(source_root)
        _run(
            settings,
            settings.app_paths,
            no_browser=args.no_browser,
            legacy_root=Path(source_root).resolve(),
        )
    except (ValueError, RuntimeError, PathResolutionError) as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def production_main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    activate_private_umask()
    root = resource_root()
    try:
        paths = AppPaths.resolve(root, production=True)
        settings = load_production_settings(paths)
        if not isinstance(settings, Settings):
            raise ValueError("生产配置未生成有效 Settings")
        _run(settings, paths, no_browser=args.no_browser, legacy_root=root)
    except (ValueError, RuntimeError, PathResolutionError) as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
