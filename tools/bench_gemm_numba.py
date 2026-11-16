# Numba CUDA edition of bench_gemm.py: same N, precisions, clock sampling and verdict.
# Numba kernels cannot issue Tensor Core MMA, so GEMMs call cuBLAS via ctypes on Numba-owned device memory.
import ctypes
import gc
import os
import statistics
import subprocess
import threading
import time

import numba
import numpy as np
from numba import cuda

SM_COUNT = 20  # RTX 3050 Ti Laptop (GA107M): 2560 CUDA cores / 128 per SM
N = 8192
WARMUP = 10
ITERS = 30

# cudaDataType / cublasComputeType_t values from the CUDA headers.
R_32F, R_16F, R_16BF = 0, 2, 14
COMPUTE_32F, COMPUTE_32F_TF32 = 68, 77
GEMM_DEFAULT = -1


def load_cublas():
    for var in ("CUDA_PATH", "CONDA_PREFIX"):
        root = os.environ.get(var)
        for sub in (("bin",), ("bin", "x64"), ("Library", "bin")):
            path = os.path.join(root, *sub) if root else ""
            if os.path.isdir(path):
                os.add_dll_directory(path)
    for name in ("cublas64_13", "cublas64_12", "cublas64_11"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    raise SystemExit("cuBLAS DLL not found; install it (conda install -c conda-forge libcublas) or set CUDA_PATH")


cublas = load_cublas()
handle = ctypes.c_void_p()


def check(status):
    if status:
        raise RuntimeError(f"cuBLAS error {status}")


check(cublas.cublasCreate_v2(ctypes.byref(handle)))


def ptr(arr):
    return ctypes.c_void_p(arr.device_ctypes_pointer.value)


def gemm(a, b, c, dtype, compute):
    alpha, beta = ctypes.c_float(1.0), ctypes.c_float(0.0)
    # Row-major C = A@B is column-major C^T = B^T A^T, so A and B swap.
    check(cublas.cublasGemmEx(
        handle, 0, 0, N, N, N, ctypes.byref(alpha),
        ptr(b), dtype, N, ptr(a), dtype, N, ctypes.byref(beta),
        ptr(c), dtype, N, compute, GEMM_DEFAULT))


def smi(*fields):
    out = subprocess.run(
        ["nvidia-smi", f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"],
        capture_output=True, text=True)
    return [v.strip() for v in out.stdout.strip().split(",")]


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_evt = threading.Event()
        self.clocks = []
        self.power = []

    def run(self):
        while not self.stop_evt.is_set():
            try:
                vals = smi("clocks.sm", "power.draw")
                self.clocks.append(int(vals[0]))
                self.power.append(float(vals[1]))
            except (ValueError, IndexError, OSError):
                pass
            time.sleep(0.05)


def bench(a, b, c, label, dtype, compute):
    for _ in range(WARMUP):
        gemm(a, b, c, dtype, compute)
    cuda.synchronize()
    s = Sampler()
    s.start()
    start = time.perf_counter()
    for _ in range(ITERS):
        gemm(a, b, c, dtype, compute)
    cuda.synchronize()
    elapsed = (time.perf_counter() - start) / ITERS
    s.stop_evt.set()
    s.join()
    flops = 2 * N**3
    tflops = flops / (elapsed * 1e12)
    clk = statistics.median(s.clocks) if s.clocks else float("nan")
    pwr = statistics.median(s.power) if s.power else float("nan")
    ops = tflops * 1e12 / (SM_COUNT * clk * 1e6) if s.clocks else float("nan")
    print(f"{label:<24} {tflops:7.2f} TFLOPS   SM clock {clk:5.0f} MHz   "
          f"~{pwr:4.1f} W   {ops:7.1f} ops/SM/clk")
    return tflops, clk, ops


rng = np.random.default_rng()


def randn32():
    return rng.standard_normal((N, N), dtype=np.float32)


def dev_fp16():
    return cuda.to_device(randn32().astype(np.float16))


def dev_bf16():
    # BF16 is the top 16 bits of FP32; NumPy has no native dtype, so carry it as uint16.
    return cuda.to_device((randn32().view(np.uint32) >> 16).astype(np.uint16))


def dev_fp32():
    return cuda.to_device(randn32())


def release():
    gc.collect()
    cuda.current_context().deallocations.clear()


dev = cuda.get_current_device()
name = dev.name.decode() if isinstance(dev.name, bytes) else dev.name
cublas_ver = ctypes.c_int()
check(cublas.cublasGetVersion_v2(handle, ctypes.byref(cublas_ver)))
print(f"Numba {numba.__version__} (cuBLAS {cublas_ver.value})")
print(f"Device: {name}")
info = smi("driver_version", "vbios_version", "power.limit")
print(f"Driver: {info[0]}   VBIOS: {info[1]}   Power limit: {info[2]} W")
print(f"\n{N}x{N} GEMM, {ITERS} iterations, median SM clock during run.\n")

results = {}

a16, b16 = dev_fp16(), dev_fp16()
c16 = cuda.device_array((N, N), dtype=np.float16)
results["FP16 in / FP32 acc"] = bench(a16, b16, c16, "FP16 in / FP32 acc", R_16F, COMPUTE_32F)
del a16, b16, c16
release()

a_bf, b_bf = dev_bf16(), dev_bf16()
c_bf = cuda.device_array((N, N), dtype=np.uint16)
results["BF16 in / FP32 acc"] = bench(a_bf, b_bf, c_bf, "BF16 in / FP32 acc", R_16BF, COMPUTE_32F)
del a_bf, b_bf, c_bf
release()

a32, b32 = dev_fp32(), dev_fp32()
c32 = cuda.device_array((N, N), dtype=np.float32)
results["TF32 in / FP32 acc"] = bench(a32, b32, c32, "TF32 in / FP32 acc", R_32F, COMPUTE_32F_TF32)
# Torch's "pedantic" row is plain COMPUTE_32F with TF32 off, not CUBLAS_COMPUTE_32F_PEDANTIC.
results["FP32 (pedantic)"] = bench(a32, b32, c32, "FP32 (pedantic)", R_32F, COMPUTE_32F)
del a32, b32, c32
release()

cublas.cublasDestroy_v2(handle)

print("\nLocked reference : FP16/BF16 ~512, TF32 ~256, FP32 ~128 ops/SM/clk")
print("Unlocked (SS0)   : FP16/BF16 ~1024 ops/SM/clk")
print("Unlocked (SS0+SS1): FP16/BF16 ~1024, TF32 ~512 ops/SM/clk")
locked_fp16 = results["FP16 in / FP32 acc"][2]
if locked_fp16 < 700:
    print(f"\nVERDICT: FP16/BF16 issue rate ~{locked_fp16:.0f} ops/SM/clk -> CARD IS LOCKED (512-class)")
elif locked_fp16 > 850:
    print(f"\nVERDICT: FP16/BF16 issue rate ~{locked_fp16:.0f} ops/SM/clk -> UNLOCKED (1024-class)")
else:
    print(f"\nVERDICT: ambiguous ({locked_fp16:.0f} ops/SM/clk) - rerun with locked clocks")
