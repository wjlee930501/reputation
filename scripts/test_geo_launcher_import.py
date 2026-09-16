"""Pytest may discover launchers without executing their isolated runner."""
import importlib.util
import os
import socket
import sys
from pathlib import Path


def test_offline_launcher_import_has_no_process_side_effects():
    path = Path(__file__).with_name("test_geo_offline.py")
    before = (os.getcwd(), dict(os.environ), list(sys.path), socket.socket.connect,
              socket.socket.connect_ex, socket.create_connection)
    spec = importlib.util.spec_from_file_location("geo_offline_import_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    after = (os.getcwd(), dict(os.environ), list(sys.path), socket.socket.connect,
             socket.socket.connect_ex, socket.create_connection)
    assert after == before
    assert callable(module.main)
