import shutil
import subprocess
from pathlib import Path
from typing import Any


def run_buf(*args: str, **kwargs: Any) -> int:
    buf_exe = shutil.which("buf")
    return subprocess.check_call([buf_exe, *args], **kwargs)


def generate_protobuf(root_dir: Path, src_dir: Path) -> None:
    proto_dir = (root_dir / "proto").absolute()
    if not proto_dir.exists():
        return
    proto_py_out_dir = (src_dir / "proto").absolute()
    proto_py_out_dir.mkdir(exist_ok=True)
    template = '{"version":"v2","plugins":[{"local":"protoc-gen-py","out":"."}]}'
    run_buf(
        "generate",
        str(proto_dir),
        f"--template={template}",
        f"-o={proto_py_out_dir}",
    )
