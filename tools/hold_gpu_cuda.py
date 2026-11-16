# Minimal GPU keep-alive: holds one CUDA context via the driver API so the dGPU stays out of D3cold.
import ctypes
import sys
import time

cuda = ctypes.CDLL("nvcuda")


def check(status):
    if status:
        sys.exit(status)  # a nonzero exit lets Task Scheduler restart it


dev, ctx = ctypes.c_int(), ctypes.c_void_p()
free, total = ctypes.c_size_t(), ctypes.c_size_t()
check(cuda.cuInit(0))
check(cuda.cuDeviceGet(ctypes.byref(dev), 0))
check(cuda.cuDevicePrimaryCtxRetain(ctypes.byref(ctx), dev))
check(cuda.cuCtxSetCurrent(ctx))

name = ctypes.create_string_buffer(100)
check(cuda.cuDeviceGetName(name, len(name), dev))
print(f"Holding GPU awake: {name.value.decode()}", flush=True)

while True:
    check(cuda.cuMemGetInfo_v2(ctypes.byref(free), ctypes.byref(total)))
    time.sleep(30)
