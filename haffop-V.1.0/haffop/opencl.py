"""Small optional OpenCL GPU workload; never falls back to a CPU device."""

from __future__ import annotations

import ctypes as C
import ctypes.util
import os
import time


class Unavailable(RuntimeError):
    """A required driver or device is not available."""


def _check(code: int) -> None:
    if code != 0:
        raise RuntimeError(f"OpenCL failed with error {code}")


class OpenCL:
    def __init__(self) -> None:
        name = "OpenCL.dll" if os.name == "nt" else ctypes.util.find_library("OpenCL")
        if not name:
            raise Unavailable("Install your GPU vendor's OpenCL driver/runtime.")
        try:
            self.lib = C.WinDLL(name) if os.name == "nt" else C.CDLL(name)
        except OSError as exc:
            raise Unavailable(f"OpenCL runtime could not load: {exc}") from exc
        ptr, uint, size = C.c_void_p, C.c_uint, C.c_size_t
        self.platforms = self._fn("clGetPlatformIDs", C.c_int, uint, C.POINTER(ptr), C.POINTER(uint))
        self.devices = self._fn("clGetDeviceIDs", C.c_int, ptr, C.c_ulonglong, uint, C.POINTER(ptr), C.POINTER(uint))
        self.info = self._fn("clGetDeviceInfo", C.c_int, ptr, uint, size, ptr, C.POINTER(size))
        self.context = self._fn("clCreateContext", ptr, ptr, uint, C.POINTER(ptr), ptr, ptr, C.POINTER(C.c_int))
        self.queue = self._fn("clCreateCommandQueue", ptr, ptr, ptr, C.c_ulonglong, C.POINTER(C.c_int))
        self.program = self._fn("clCreateProgramWithSource", ptr, ptr, uint, C.POINTER(C.c_char_p), ptr, C.POINTER(C.c_int))
        self.build = self._fn("clBuildProgram", C.c_int, ptr, uint, C.POINTER(ptr), C.c_char_p, ptr, ptr)
        self.kernel = self._fn("clCreateKernel", ptr, ptr, C.c_char_p, C.POINTER(C.c_int))
        self.buffer = self._fn("clCreateBuffer", ptr, ptr, C.c_ulonglong, size, ptr, C.POINTER(C.c_int))
        self.arg = self._fn("clSetKernelArg", C.c_int, ptr, uint, size, ptr)
        self.launch = self._fn("clEnqueueNDRangeKernel", C.c_int, ptr, ptr, uint, ptr, C.POINTER(size), ptr, uint, ptr, ptr)
        self.finish = self._fn("clFinish", C.c_int, ptr)
        self.read = self._fn("clEnqueueReadBuffer", C.c_int, ptr, ptr, uint, size, size, ptr, uint, ptr, ptr)
        self.release = {
            kind: self._fn("clRelease" + kind, C.c_int, ptr)
            for kind in ("Context", "CommandQueue", "Program", "Kernel", "MemObject")
        }

    def _fn(self, name, result, *args):
        fn = getattr(self.lib, name)
        fn.restype, fn.argtypes = result, list(args)
        return fn

    def gpu_devices(self) -> list[tuple[C.c_void_p, str]]:
        count = C.c_uint()
        code = self.platforms(0, None, C.byref(count))
        if code == -1001:
            raise Unavailable("No OpenCL platform. Install your GPU vendor's OpenCL runtime.")
        _check(code)
        platforms = (C.c_void_p * count.value)()
        _check(self.platforms(count, platforms, None))
        found = []
        for platform in platforms:
            n = C.c_uint()
            code = self.devices(platform, 4, 0, None, C.byref(n))  # CL_DEVICE_TYPE_GPU
            if code == -1:
                continue
            _check(code)
            devices = (C.c_void_p * n.value)()
            _check(self.devices(platform, 4, n, devices, None))
            for device in devices:
                name = C.create_string_buffer(1024)
                _check(self.info(device, 0x102B, len(name), name, None))
                found.append((C.c_void_p(device), name.value.decode("utf-8", "replace")))
        if not found:
            raise Unavailable("No OpenCL GPU device. CPU/software fallbacks are not benchmarked.")
        return found


def benchmark(seconds: float, index: int) -> dict:
    cl = OpenCL()
    devices = cl.gpu_devices()
    if not 0 <= index < len(devices):
        raise Unavailable(f"OpenCL GPU index {index} unavailable. Devices: "
                          + ", ".join(f"{i}: {name}" for i, (_, name) in enumerate(devices)))
    device, name = devices[index]
    resources = []
    err = C.c_int()

    def own(kind, handle):
        _check(err.value)
        if not handle:
            raise RuntimeError(f"OpenCL returned an empty {kind}")
        resources.append((kind, handle))
        return handle

    try:
        ctx = own("Context", cl.context(None, 1, C.byref(device), None, None, C.byref(err)))
        queue = own("CommandQueue", cl.queue(ctx, device, 0, C.byref(err)))
        source = C.c_char_p(b"""
        __kernel void work(__global uint *out) {
            uint i = get_global_id(0), v = i;
            for (uint j = 0; j < 256; ++j) v = (v * 1664525u + 1013904223u) ^ (v >> 13);
            out[i] = v;
        }""")
        program = own("Program", cl.program(ctx, 1, C.byref(source), None, C.byref(err)))
        _check(cl.build(program, 1, C.byref(device), None, None, None))
        kernel = own("Kernel", cl.kernel(program, b"work", C.byref(err)))
        count = 262144
        buf = C.c_void_p(own("MemObject", cl.buffer(ctx, 1, count * 4, None, C.byref(err))))
        _check(cl.arg(kernel, 0, C.sizeof(buf), C.byref(buf)))
        size = C.c_size_t(count)
        _check(cl.launch(queue, kernel, 1, None, C.byref(size), None, 0, None, None))
        _check(cl.finish(queue))
        start = time.perf_counter()
        loops = 0
        while time.perf_counter() - start < seconds:
            _check(cl.launch(queue, kernel, 1, None, C.byref(size), None, 0, None, None))
            _check(cl.finish(queue))
            loops += 1
        elapsed = time.perf_counter() - start
        sample = (C.c_uint * 32)()
        _check(cl.read(queue, buf, 1, 0, C.sizeof(sample), sample, 0, None, None))
        for i, actual in enumerate(sample):
            expected = i
            for _ in range(256):
                expected = ((expected * 1664525 + 1013904223) ^ (expected >> 13)) & 0xFFFFFFFF
            if actual != expected:
                raise RuntimeError(f"GPU verification failed at sample {i}")
        return {"device": name, "score": loops * count * 256 / elapsed / 1e6,
                "unit": "M integer updates/s", "elapsed": elapsed,
                "detail": "OpenCL integer compute; 32 output samples verified. Not a gaming/FPS or VRAM test."}
    finally:
        for kind, handle in reversed(resources):
            cl.release[kind](handle)
