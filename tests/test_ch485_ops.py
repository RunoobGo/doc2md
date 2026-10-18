"""CH-485 / 运维与入口：Windows offline guard、CLI 参数、权限、watch 下限。"""
from __future__ import annotations

import socket

import pytest

import doc2md.security.netguard as ng
from doc2md.cli import _ensure_private_root, _watch_stable_seconds, build_parser


class _FakeSocket:
    """模拟 Windows 的 socket.socket：有 connect/connect_ex/sendto，无 sendmsg。"""

    def connect(self, *a):  # pragma: no cover - 仅作为可替换原语
        pass

    def connect_ex(self, *a):  # pragma: no cover
        pass

    def sendto(self, *a):  # pragma: no cover
        pass


@pytest.mark.gate
def test_netguard_install_tolerates_missing_sendmsg(monkeypatch):
    fake = type("FakeSocket", (_FakeSocket,), {})
    monkeypatch.setattr(socket, "socket", fake)
    ng._ORIG.clear()
    try:
        ng._install({"127.0.0.1"})
        assert fake.connect is ng._guarded_connect
        assert "sendmsg" not in ng._ORIG
        assert ng.sendmsg_supported() is False
        ng._uninstall()
        assert fake.connect is not ng._guarded_connect
        assert ng._ORIG == {}
    finally:
        ng._ORIG.clear()


@pytest.mark.gate
def test_cli_common_options_survive_nested_subparsers():
    cases = [
        (["serve", "--root", "/x", "token", "--role", "admin"], "/x"),
        (["serve", "token", "--root", "/x", "--role", "admin"], "/x"),
        (["backup", "--root", "/x", "restore", "src", "--force"], "/x"),
        (["backup", "restore", "src", "--root", "/x", "--force"], "/x"),
        (["tokenizer", "--root", "/x", "verify", "--manifest", "m", "--gguf", "g"], "/x"),
        (["tokenizer", "verify", "--manifest", "m", "--gguf", "g", "--root", "/x"], "/x"),
    ]
    for argv, expected in cases:
        args = build_parser().parse_args(argv)
        assert getattr(args, "root", None) == expected, (argv, vars(args))


def test_cli_private_root_mode(tmp_path):
    root = tmp_path / "work"
    _ensure_private_root(root)
    assert root.is_dir()
    assert (root.stat().st_mode & 0o777) == 0o700


def test_watch_stable_seconds_nonzero_floor():
    cfg = {"watch": {"stable_seconds": 0}}
    assert _watch_stable_seconds(cfg) == 0.1
    cfg["watch"]["stable_seconds"] = -3
    assert _watch_stable_seconds(cfg) == 0.1
    cfg["watch"]["stable_seconds"] = 2.5
    assert _watch_stable_seconds(cfg) == 2.5
