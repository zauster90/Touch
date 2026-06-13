"""One-shot installer for the TouchAPI component.

Drag this file into a TouchDesigner network — TD turns it into a Text DAT —
then right-click the DAT and choose **Run Script**. It builds
`/project1/TouchAPI` from `toe/src/td_api.py`, wires the runtime shim,
bootstrap, and token-rotation callback, appends the custom parameters, and
starts the HTTP server. No Component Editor steps, no hand-set parameters.

Re-running it tears down any existing TouchAPI (stopping its server first) and
rebuilds from scratch, so it is safe to run repeatedly.

Source of truth: `toe/src/td_api.py`. This installer never embeds a copy of the
server — it reads it off disk so the two cannot drift. The only text embedded
here is the tiny runtime shim and the lifecycle callbacks, which have no
standalone source file.

Once it reports READY you can right-click the `TouchAPI` COMP and choose
**Save Component .tox...** to produce a binary others can simply drag in.
"""

# --- configuration -----------------------------------------------------------
PARENT_PATH = "/project1"   # where TouchAPI gets created
COMP_NAME = "TouchAPI"
PORT = 44444

# Only needed if the installer can't find td_api.py from its own file path
# (e.g. you pasted this into the textport instead of dragging the file in).
# Set the absolute path to the repo's `toe/` folder, e.g. r"C:/dev/Touch/toe".
TOE_DIR_OVERRIDE = ""

# --- embedded text (no standalone source file) -------------------------------
SHIM_SRC = '''# td_runtime shim — exposes TD's globals as a module so td_api_src
# can `import td_runtime` both inside TD and in standalone tests.
import sys, types
m = types.ModuleType("td_runtime")
m.op = op
m.ops = ops
m.ui = ui
m.ParMode = ParMode  # needed by /bind to switch a parameter to expression mode
sys.modules["td_runtime"] = m
'''

BOOTSTRAP_SRC = '''import sys

def onStart():
    op('td_runtime_shim').run()
    # td_layout is a pure-Python sibling DAT used by the /layout endpoint. TD
    # can import sibling DATs directly, but register it explicitly so
    # td_api_src's `import td_layout` is bulletproof across TD versions.
    sys.modules['td_layout'] = mod('td_layout')
    mod('td_api_src').start_server()
    parent().par.Status = f"READY @ 127.0.0.1:{parent().par.Port.eval()}"

def onExit():
    try:
        mod('td_api_src').stop_server()
    finally:
        parent().par.Status = "stopped"

def onFrameStart(frame):
    # Drain HTTP-worker-thread work onto TD's main cook thread every frame.
    # TD's Python API is main-thread-only; this is what makes it safe.
    mod('td_api_src').drain_main_queue()
'''

ROTATE_CALLBACK_SRC = '''def onPulse(par):
    mod('td_api_src').rotate_token()
'''


# --- helpers -----------------------------------------------------------------
def _log(msg):
    print(f"[TouchAPI install] {msg}")


def _find_toe_dir():
    import os
    if TOE_DIR_OVERRIDE:
        return TOE_DIR_OVERRIDE
    # When dragged in, this script is a Text DAT whose `file` par points at it.
    try:
        f = me.par.file.eval()  # noqa: F821  (me is injected by TD)
    except Exception:
        f = ""
    if f:
        return os.path.dirname(os.path.abspath(f))  # the dir holding install.py == toe/
    raise RuntimeError(
        "Cannot locate td_api.py automatically. Set TOE_DIR_OVERRIDE at the top "
        "of this script to the absolute path of the repo's toe/ folder."
    )


def _read_src(filename):
    import os
    src_path = os.path.join(_find_toe_dir(), "src", filename)
    if not os.path.exists(src_path):
        raise RuntimeError(f"{filename} not found at {src_path}")
    with open(src_path, "r", encoding="utf-8") as fh:
        return fh.read()


def _set_python(dat):
    """Force a DAT's language to Python where that parameter exists."""
    par = getattr(dat.par, "language", None)
    if par is not None:
        try:
            par.val = "python"
        except Exception:
            pass


def _teardown(root):
    """Stop and remove any existing TouchAPI so the port frees up before rebuild."""
    existing = root.op(COMP_NAME)
    if not existing:
        return
    try:
        existing.op("td_api_src").module.stop_server()
        _log("stopped previous server")
    except Exception as exc:  # noqa: BLE001
        _log(f"previous server not stopped ({exc!r}) — continuing")
    existing.destroy()


def _add_params(comp):
    page = comp.appendCustomPage("Settings")
    port_p = page.appendInt("Port", label="Port")[0]
    port_p.default = PORT
    port_p.val = PORT
    port_p.readOnly = True
    status_p = page.appendStr("Status", label="Status")[0]
    status_p.default = "stopped"
    status_p.val = "stopped"
    status_p.enable = False
    page.appendPulse("Rotate", label="Rotate token")


def _wire_rotate(comp):
    """Best-effort: a Parameter Execute DAT that calls rotate_token on pulse.
    The server runs fine without this; only token rotation depends on it."""
    try:
        pe = comp.create(parameterexecuteDAT, "param_exec")  # noqa: F821
        pe.text = ROTATE_CALLBACK_SRC
        _set_python(pe)
        pe.par.op = comp
        for pname in ("custom", "pulse", "active"):
            p = getattr(pe.par, pname, None)
            if p is not None:
                p.val = True
        pe.nodeX, pe.nodeY = 0, -300
    except Exception as exc:  # noqa: BLE001
        _log(f"Rotate callback not wired ({exc!r}); rotate the token from the "
             f"textport with: op('{comp.path}/td_api_src').module.rotate_token()")


def build():
    api_src = _read_src("td_api.py")
    layout_src = _read_src("td_layout.py")

    root = op(PARENT_PATH) or me.parent()  # noqa: F821
    _teardown(root)

    comp = root.create(containerCOMP, COMP_NAME)  # noqa: F821

    src_dat = comp.create(textDAT, "td_api_src")  # noqa: F821
    src_dat.text = api_src
    _set_python(src_dat)
    src_dat.nodeX, src_dat.nodeY = 0, 0

    layout_dat = comp.create(textDAT, "td_layout")  # noqa: F821
    layout_dat.text = layout_src
    _set_python(layout_dat)
    layout_dat.nodeX, layout_dat.nodeY = 0, 150

    shim = comp.create(textDAT, "td_runtime_shim")  # noqa: F821
    shim.text = SHIM_SRC
    _set_python(shim)
    shim.nodeX, shim.nodeY = 0, -150

    boot = comp.create(executeDAT, "bootstrap")  # noqa: F821
    boot.text = BOOTSTRAP_SRC
    _set_python(boot)
    # start: launch server; exit: stop it; framestart: drain the main-thread
    # queue every frame (required for thread-safe op access).
    for pname in ("start", "exit", "framestart", "active"):
        p = getattr(boot.par, pname, None)
        if p is not None:
            p.val = True
    boot.nodeX, boot.nodeY = 0, -450

    _add_params(comp)
    _wire_rotate(comp)

    # Start immediately so it's READY without restarting the project.
    shim.run()
    comp.op("td_api_src").module.start_server()
    comp.par.Status = f"READY @ 127.0.0.1:{PORT}"
    _log(f"READY @ 127.0.0.1:{PORT} — built {comp.path}")
    _log("To distribute: right-click TouchAPI -> Save Component .tox...")
    return comp


# Auto-run when executed via right-click -> Run Script.
build()
