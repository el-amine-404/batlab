"""Which GPU this computer has, if any."""

import glob
import os
import site


def detect() -> tuple[str, int]:
    """(name, memory in MB), or ("", 0) when there is no usable NVIDIA GPU."""
    try:
        import torch
    except ImportError:
        return "", 0
    if not torch.cuda.is_available():
        return "", 0
    props = torch.cuda.get_device_properties(0)
    return props.name, int(props.total_memory / 1024 / 1024)


def cuda_library_path() -> str:
    """The CUDA libraries pip installed with torch (cuBLAS, cuDNN), for
    faster-whisper's CTranslate2, which looks for them on LD_LIBRARY_PATH."""
    dirs = []
    for base in site.getsitepackages():
        dirs += glob.glob(os.path.join(base, "nvidia", "*", "lib"))
    return os.pathsep.join(dirs + [os.environ.get("LD_LIBRARY_PATH", "")]).strip(os.pathsep)
