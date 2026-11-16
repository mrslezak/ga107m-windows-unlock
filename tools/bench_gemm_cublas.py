# Dependency-light edition of bench_gemm.py: same N, precisions, clock sampling and verdict.
# Uses only NumPy plus the CUDA runtime and cuBLAS DLLs through ctypes (no Torch, no Numba).
import ctypes
import os
import statistics
import subprocess
import threading
import time

import numpy as np

SM_COUNT = 20  # RTX 3050 Ti Laptop (GA107M): 2560 CUDA cores / 128 per SM
N = 8192
WARMUP = 10
ITERS = 30

# cudaDataType / cublasComputeType_t values from the CUDA headers.
R_32F, R_16F, R_16BF = 0, 2, 14
COMPUTE_32F, COMPUTE_32F_TF32 = 68, 77
GEMM_DEFAULT = -1
MEMCPY_H2D = 1


def add_dll_dirs():
    for var in ("CUDA_PATH", "CONDA_PREFIX"):
        root = os.environ.get(var)
        for sub in (("bin",), ("bin", "x64"), ("Library", "bin")):
            path = os.path.join(root, *sub) if root else ""
            if os.path.isdir(path):
                os.add_dll_directory(path)


def load(names, hint):
    for name in names:
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    raise SystemExit(f"{names[0]} not found; {hint}")


add_dll_dirs()
cudart = load(("cudart64_13", "cudart64_12", "cudart64_110"),
              "install the CUDA runtime (conda install -c conda-forge cuda-cudart) or set CUDA_PATH")
cublas = load(("cublas64_13", "cublas64_12", "cublas64_11"),
              "install cuBLAS (conda install -c conda-forge libcublas) or set CUDA_PATH")

cudart.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
cudart.cudaMemcpy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
cudart.cudaFree.argtypes = [ctypes.c_void_p]


def check(status, what="CUDA"):
    if status:
        raise RuntimeError(f"{what} error {status}")


class DeviceBuffer:
    def __init__(self, nbytes, host=None):
        self.ptr = ctypes.c_void_p()
        check(cudart.cudaMalloc(ctypes.byref(self.ptr), nbytes))
        if host is not None:
            host = np.ascontiguousarray(host)
            check(cudart.cudaMemcpy(self.ptr, host.ctypes.data_as(ctypes.c_void_p), nbytes, MEMCPY_H2D))

    def free(self):
        check(cudart.cudaFree(self.ptr))


check(cudart.cudaSetDevice(0))
handle = ctypes.c_void_p()
check(cublas.cublasCreate_v2(ctypes.byref(handle)), "cuBLAS")


def sync():
    check(cudart.cudaDeviceSynchronize())


def gemm(a, b, c, dtype, compute):
    alpha, beta = ctypes.c_float(1.0), ctypes.c_float(0.0)
    # Row-major C = A@B is column-major C^T = B^T A^T, so A and B swap.
    check(cublas.cublasGemmEx(
        handle, 0, 0, N, N, N, ctypes.byref(alpha),
        b.ptr, dtype, N, a.ptr, dtype, N, ctypes.byref(beta),
        c.ptr, dtype, N, compute, GEMM_DEFAULT), "cuBLAS")


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
    sync()
    s = Sampler()
    s.start()
    start = time.perf_counter()
    for _ in range(ITERS):
        gemm(a, b, c, dtype, compute)
    sync()
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


def buf_fp16():
    return DeviceBuffer(N * N * 2, randn32().astype(np.float16))


def buf_bf16():
    # BF16 is the top 16 bits of FP32; NumPy has no native dtype, so carry it as uint16.
    return DeviceBuffer(N * N * 2, (randn32().view(np.uint32) >> 16).astype(np.uint16))


def buf_fp32():
    return DeviceBuffer(N * N * 4, randn32())


def run_case(label, make, out_bytes, dtype, compute):
    a, b, c = make(), make(), DeviceBuffer(out_bytes)
    result = bench(a, b, c, label, dtype, compute)
    for buf in (a, b, c):
        buf.free()
    return result


cublas_ver = ctypes.c_int()
check(cublas.cublasGetVersion_v2(handle, ctypes.byref(cublas_ver)), "cuBLAS")
print(f"cuBLAS {cublas_ver.value} via ctypes (NumPy {np.__version__})")
info = smi("name", "driver_version", "vbios_version", "power.limit")
print(f"Device: {info[0]}")
print(f"Driver: {info[1]}   VBIOS: {info[2]}   Power limit: {info[3]} W")
print(f"\n{N}x{N} GEMM, {ITERS} iterations, median SM clock during run.\n")

results = {}
results["FP16 in / FP32 acc"] = run_case("FP16 in / FP32 acc", buf_fp16, N * N * 2, R_16F, COMPUTE_32F)
results["BF16 in / FP32 acc"] = run_case("BF16 in / FP32 acc", buf_bf16, N * N * 2, R_16BF, COMPUTE_32F)

# TF32 and pedantic FP32 share one set of FP32 inputs, as in bench_gemm.py.
a32, b32, c32 = buf_fp32(), buf_fp32(), DeviceBuffer(N * N * 4)
results["TF32 in / FP32 acc"] = bench(a32, b32, c32, "TF32 in / FP32 acc", R_32F, COMPUTE_32F_TF32)
results["FP32 (pedantic)"] = bench(a32, b32, c32, "FP32 (pedantic)", R_32F, COMPUTE_32F_PEDANTIC)
for buf in (a32, b32, c32):
    buf.free()

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
