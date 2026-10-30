import os
import sys
import tempfile

if "--screenshot" in sys.argv:  # a screenshot run never touches a running instance, its config, or the live audio graph
    _t = tempfile.mkdtemp(prefix="vmshot_")
    os.environ.setdefault("VERTIMEETER_CONFIG_DIR", _t)
    os.environ.setdefault("VERTIMEETER_SOCK", os.path.join(_t, "engine.sock"))
    os.environ.setdefault("VERTIMEETER_CLIENT", "VMShot")
    os.environ.setdefault("VERTIMEETER_NOLINK", "1")

from .config import VERSION  # noqa: E402

__all__ = ["VERSION"]
