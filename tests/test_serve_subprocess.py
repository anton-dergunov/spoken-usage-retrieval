import json
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from test_index_search_api import indexed_data


def test_serve_subprocess_becomes_ready_searches_and_stops_cleanly(tmp_path):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", 0))
        except PermissionError:
            pytest.skip("sandbox does not permit loopback sockets")
        port = listener.getsockname()[1]
    executable = Path(sys.executable).with_name("speech-retrieval")
    process = subprocess.Popen(
        [
            str(executable),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--data-dir",
            str(data_dir),
            "--catalogue-dir",
            str(catalogue_dir),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while True:
            try:
                with urlopen(f"{base_url}/api/v1/health/ready", timeout=0.5) as response:
                    assert response.status == 200
                break
            except (URLError, ConnectionError):
                if process.poll() is not None or time.monotonic() >= deadline:
                    stdout, stderr = process.communicate(timeout=1)
                    raise AssertionError(f"service failed to become ready\n{stdout}\n{stderr}")
                time.sleep(0.05)
        with urlopen(f"{base_url}/api/v1/search?language=es&q=la%20verdad", timeout=2) as response:
            result = json.load(response)
        assert result["results"]
    finally:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=5)
    assert process.returncode == 0
