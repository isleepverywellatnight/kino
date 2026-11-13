"""Native video player for KINO: ctypes binding to the bundled libmpv.

libmpv and its dependencies are vendored in vendor/libmpv (see
tools/vendor_libmpv.py) and bundled into KINO.app; IINA is never used at run
time. This module covers the client API (commands, properties) and rendering
into an OpenGL framebuffer; the window integration comes in a later step.
"""
import ctypes
import os
from pathlib import Path

import sys

PROJECT_DIR = Path(__file__).resolve().parent

MPV_FORMAT_FLAG = 3
MPV_FORMAT_INT64 = 4
MPV_FORMAT_DOUBLE = 5

MPV_RENDER_PARAM_INVALID = 0
MPV_RENDER_PARAM_API_TYPE = 1
MPV_RENDER_PARAM_OPENGL_INIT_PARAMS = 2
MPV_RENDER_PARAM_OPENGL_FBO = 3
MPV_RENDER_PARAM_FLIP_Y = 4
MPV_RENDER_UPDATE_FRAME = 1

GL_FRAMEBUFFER_BINDING = 0x8CA6


class MpvError(RuntimeError):
    pass


class RenderParam(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("data", ctypes.c_void_p)]


GET_PROC_ADDRESS = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)


class OpenGLInitParams(ctypes.Structure):
    _fields_ = [("get_proc_address", GET_PROC_ADDRESS), ("get_proc_address_ctx", ctypes.c_void_p)]


class OpenGLFbo(ctypes.Structure):
    _fields_ = [("fbo", ctypes.c_int), ("w", ctypes.c_int), ("h", ctypes.c_int), ("internal_format", ctypes.c_int)]


def libmpv_dir():
    """Folder holding libmpv.2.dylib and its dependencies, inside KINO only."""
    candidates = []
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS))
        candidates.append(Path(sys.executable).resolve().parent.parent / "Frameworks")
    candidates.append(PROJECT_DIR / "vendor" / "libmpv")
    for folder in candidates:
        if (folder / "libmpv.2.dylib").is_file():
            return folder
    return None


class LibmpvUnavailable(RuntimeError):
    pass


_lib = None
_gl = None


def lib():
    global _lib
    if _lib is None:
        folder = libmpv_dir()
        if folder is None:
            raise LibmpvUnavailable("libmpv n'est pas présente dans KINO.")
        # Each vendored library carries an @loader_path rpath: no preloading needed.
        _lib = ctypes.CDLL(str(folder / "libmpv.2.dylib"))
        L = _lib
        L.mpv_client_api_version.restype = ctypes.c_ulong
        L.mpv_create.restype = ctypes.c_void_p
        L.mpv_error_string.restype = ctypes.c_char_p
        L.mpv_error_string.argtypes = [ctypes.c_int]
        for name in ("mpv_set_option_string", "mpv_set_property_string"):
            getattr(L, name).argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        L.mpv_initialize.argtypes = [ctypes.c_void_p]
        L.mpv_command.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
        L.mpv_get_property.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        L.mpv_get_property_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        L.mpv_get_property_string.restype = ctypes.c_void_p
        L.mpv_free.argtypes = [ctypes.c_void_p]
        L.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
        L.mpv_render_context_create.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(RenderParam)]
        L.mpv_render_context_update.argtypes = [ctypes.c_void_p]
        L.mpv_render_context_update.restype = ctypes.c_uint64
        L.mpv_render_context_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(RenderParam)]
        L.mpv_render_context_report_swap.argtypes = [ctypes.c_void_p]
        L.mpv_render_context_free.argtypes = [ctypes.c_void_p]
    return _lib


def gl():
    global _gl
    if _gl is None:
        _gl = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
        _gl.glGetIntegerv.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_int)]
    return _gl


_libc = None


def _dlsym(handle, name):
    """The C library is opened when the player first draws, not on import: on
    Windows there is no « CDLL(None) », and the server imports this module."""
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None)
        _libc.dlsym.restype = ctypes.c_void_p
        _libc.dlsym.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    return _libc.dlsym(handle, name)


@GET_PROC_ADDRESS
def _get_proc_address(_ctx, name):
    return _dlsym(gl()._handle, name)


class Mpv:
    def __init__(self, **options):
        L = lib()
        self.handle = L.mpv_create()
        if not self.handle:
            raise MpvError("mpv_create failed")
        defaults = {"vo": "libmpv", "hwdec": "videotoolbox", "keep-open": "yes", "idle": "yes",
                    "input-default-bindings": "no", "osc": "no", "terminal": "no", "config": "no"}
        defaults.update({k.replace("_", "-"): v for k, v in options.items()})
        for name, value in defaults.items():
            self._check(L.mpv_set_option_string(self.handle, name.encode(), str(value).encode()), name)
        self._check(L.mpv_initialize(self.handle), "initialize")
        self.render_ctx = None

    def _check(self, code, what):
        if code < 0:
            raise MpvError(f"{what}: {lib().mpv_error_string(code).decode()}")

    def command(self, *args):
        argv = (ctypes.c_char_p * (len(args) + 1))(*[str(a).encode() for a in args], None)
        self._check(lib().mpv_command(self.handle, argv), args[0])

    def set(self, name, value):
        value = {True: "yes", False: "no"}.get(value, value)
        self._check(lib().mpv_set_property_string(self.handle, name.encode(), str(value).encode()), name)

    def get_string(self, name):
        ptr = lib().mpv_get_property_string(self.handle, name.encode())
        if not ptr:
            return None
        try:
            return ctypes.string_at(ptr).decode("utf-8", "replace")
        finally:
            lib().mpv_free(ptr)

    def get_double(self, name):
        value = ctypes.c_double()
        return value.value if lib().mpv_get_property(self.handle, name.encode(), MPV_FORMAT_DOUBLE, ctypes.byref(value)) >= 0 else None

    def get_flag(self, name):
        value = ctypes.c_int()
        return bool(value.value) if lib().mpv_get_property(self.handle, name.encode(), MPV_FORMAT_FLAG, ctypes.byref(value)) >= 0 else None

    # --- OpenGL rendering (call with the target GL context current) ---
    def create_render_context(self):
        self._init_params = OpenGLInitParams(_get_proc_address, None)
        params = (RenderParam * 3)(
            RenderParam(MPV_RENDER_PARAM_API_TYPE, ctypes.cast(ctypes.c_char_p(b"opengl"), ctypes.c_void_p)),
            RenderParam(MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, ctypes.cast(ctypes.pointer(self._init_params), ctypes.c_void_p)),
            RenderParam(MPV_RENDER_PARAM_INVALID, None),
        )
        ctx = ctypes.c_void_p()
        self._check(lib().mpv_render_context_create(ctypes.byref(ctx), self.handle, params), "render context")
        self.render_ctx = ctx

    def frame_ready(self):
        return bool(self.render_ctx) and bool(lib().mpv_render_context_update(self.render_ctx) & MPV_RENDER_UPDATE_FRAME)

    def render(self, width, height):
        fbo_id = ctypes.c_int(0)
        gl().glGetIntegerv(GL_FRAMEBUFFER_BINDING, ctypes.byref(fbo_id))
        fbo = OpenGLFbo(fbo_id.value, int(width), int(height), 0)
        flip = ctypes.c_int(1)
        params = (RenderParam * 3)(
            RenderParam(MPV_RENDER_PARAM_OPENGL_FBO, ctypes.cast(ctypes.pointer(fbo), ctypes.c_void_p)),
            RenderParam(MPV_RENDER_PARAM_FLIP_Y, ctypes.cast(ctypes.pointer(flip), ctypes.c_void_p)),
            RenderParam(MPV_RENDER_PARAM_INVALID, None),
        )
        lib().mpv_render_context_render(self.render_ctx, params)

    def close(self):
        if self.render_ctx:
            lib().mpv_render_context_free(self.render_ctx)
            self.render_ctx = None
        if self.handle:
            lib().mpv_terminate_destroy(self.handle)
            self.handle = None


def api_version():
    v = lib().mpv_client_api_version()
    return f"{v >> 16}.{v & 0xffff}"


def check():
    """Self-check: load the bundled libmpv and open a headless player."""
    folder = libmpv_dir()
    if folder is None:
        raise LibmpvUnavailable("libmpv n'est pas présente dans KINO.")
    player = Mpv(vo="null", ao="null")
    try:
        version = player.get_string("mpv-version")
    finally:
        player.close()
    return {"api": api_version(), "mpv": version, "path": str(folder)}
