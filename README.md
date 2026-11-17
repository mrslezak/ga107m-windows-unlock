# GA107M Windows GPU Unlock (RTX 3050 / 3050 Ti Laptop)

Quickstart (recommended):

1. Run `tools\new-machine-wizard.cmd`.
2. Choose **Step 1** and write/stage the probe USB.
3. Boot once from USB and collect `REPORT.TXT`.
4. Run the wizard again and choose **Step 2** to build/write final USB.
5. After Windows boot, set up the SYSTEM hold task (recommended below).

If firmware assets are missing, the flow prompts you to run
`tools\firmware\build-assets.cmd`.

## Required Before Step 1

- Use a dedicated 4 GB+ USB drive.
- The flow can format that USB as FAT32 (`-UsbFormat`), which erases files.
- Back up the USB first.

Asset pipeline details (pinned inputs, hashes, validation):
[docs/ASSETS.en.md](docs/ASSETS.en.md).

## Defaults Used by the Wizard

- Profile default is `entryLatchedSs1Full` (SS0 + SS1 path).
- Step 1 captures probe output; Step 2 builds the final unlock from `REPORT.TXT`.
- Manual decode/preflight tools are still available for advanced workflows.

**English only** | Upstream: [impacifist/Windows-GPU-UNLOCK](https://github.com/impacifist/Windows-GPU-UNLOCK) (RTX 3090 FE) — this repository is a fork carrying a port to a laptop GPU.

This remains an experimental UEFI unlock/direct handoff flow with documented
results on two GA107M laptop GPU systems (RTX 3050 Ti Laptop GPU and RTX 3050
Laptop GPU). This repository is source-only: firmware assets and built EFI
binaries are not redistributed.

## Persistence on Windows (Recommended Path)

Persistence is not automatic on laptops: Windows runtime D3cold can power-gate
the dGPU, and selector values reset on a new cold init.

Use this path first:

1. Run `tools/setup-hold-task-at-boot.ps1` from an elevated PowerShell.
2. This installs a SYSTEM startup Scheduled Task running
   `tools/hold_gpu_cuda.py` (AC-only).
3. Confirm the task is running after boot/logon.

Alternative path (less reliable on some systems):

- `tools/setup-hold-startup.ps1` (per-user Startup shortcut running
  `tools/hold_gpu_windowed.py`).

Display sleep can interfere with persistence. `powercfg /change
monitor-timeout-ac <value>` uses minutes (`0` = Never):

```powershell
# Disable monitor sleep on AC for testing (0 = Never)
powercfg /change monitor-timeout-ac 0

# Restore monitor sleep after 30 minutes (AC)
powercfg /change monitor-timeout-ac 30
```

BitLocker note: decryption is not required. You can temporarily suspend
protectors for a limited reboot window:

```powershell
manage-bde -protectors -enable C:
manage-bde -protectors -disable C: -RebootCount 3
```

Helper script in this repo:

- `tools/bitlocker-temporary-suspend.ps1`

More detail: [docs/GA107M-PERSISTENCE.en.md](docs/GA107M-PERSISTENCE.en.md).

## Benchmark Results

RTX3050M 4GB VRAM reported results (current machine):

| Workload | Locked | Unlocked |
|---|---|---|
| FP16 in / FP32 acc | 405 ops/SM/clk (13.9 TFLOPS) | **763 ops/SM/clk (21.0 TFLOPS)** |
| BF16 in / FP32 acc | 404 ops/SM/clk (14.1 TFLOPS) | **780 ops/SM/clk (21.9 TFLOPS)** |
| TF32 in / FP32 acc | 203 ops/SM/clk (7.0 TFLOPS) | **394 ops/SM/clk (11.5 TFLOPS)** |
| FP32 pedantic | unchanged | **unchanged** |

Notes:

- These values are lower than the earlier fork report below, which is expected
  across different laptop firmware/power/cooling/governor behavior.
- These runs used temporary BitLocker protector suspension, not decryption.

Prior fork report (reference machine, 2026-10-25):

| 8192³ GEMM (clock-normalized) | Locked | Unlocked (SS0-only) | Unlocked (SS0+SS1) |
|---|---|---|---|
| FP16 in / FP32 acc | 513 ops/SM/clk (16.7 TFLOPS @ 1657 MHz, 59 W) | **987 ops/SM/clk (25.8 TFLOPS @ 1305 MHz, 48.5 W)** | **969 ops/SM/clk (25.4 TFLOPS)** |
| BF16 in / FP32 acc | 507 ops/SM/clk | **962 ops/SM/clk** | **984 ops/SM/clk (26.3 TFLOPS)** |
| TF32 in / FP32 acc | 252 ops/SM/clk | unchanged (SS1 preserved) | **503 ops/SM/clk (13.1 TFLOPS)** |
| FP32 pedantic | ~170 ops/SM/clk | unchanged | unchanged |

This is still a narrow validation set (specific laptop firmware/card
combinations), not a broad compatibility claim across all GA107M laptops.
Do not attempt this on another laptop without running preflights first.
Read [docs/GA107M.en.md](docs/GA107M.en.md) and
[docs/COMPATIBILITY.en.md](docs/COMPATIBILITY.en.md).

## Original Fork Author Notes

This was done because I wanted to experiment with it and see if it can speed up some operations for tinier ML tasks. This isn't useful for every AI task, so depending on your needs it's a waste of time, but development and fine-tuning and image-gen benefit to a degree when not bottlenecked by other things. This might also be patched in the future by some drivers as with some things in the past.

If you want to try it on your own machine, I recommend using AI agents to go through and understand and experiment at first, without writes (hence the pre-flights), but it's best that you know what you're doing to a degree and understand how Optimus works on laptops and what things might get in the way and whether the card is fully cold before boot and so on. It's less risky if you do this on Linux and try the method in other repos, as they also handle the persistence better without needing to keep the card active the same way, and without UEFI and boot voodoo. The risk itself is low since it's wiped out every time the GPU is turned off and restarted in many cases, so long as you're not messing with the wrong things.

**One more note:** Power consumption seems to be down by 25-50% in many cases. Verified by starting windows with the change, then letting the GPU reset by not holding the card, then trying after. Instance -> Gaming (shader-bound, 65% util, 1950Mhz): FPS unchanged, power −25% (40→30 W) in one A/B session — not a verified claim and I won't go through more things, but consistent with the governor observations in the GEMM data. The power draw even with FPS limits was also lower. This could be some measurement window variance or some threshold being crossed somewhere else, but I'm using a more stable voltage curve than stock, so it might be that it can just draw less in some specific cases, with util, clock speed and FPS being exact otherwise.

## What is in this fork

| Path | Purpose |
|---|---|
| `unlock/` | The EFI application sources; three build modes (below) |
| `unlock/vendor/` | Vendored transaction core (GA107 laptop-FWSEC fix ported in) |
| `tools/ga107m-parse-preflight.py` | Turns a probe REPORT.TXT into exact-card pins |
| `unlock/tests/test_vbios_dmem.c` | Live-geometry regression for the FWSEC DMEM round-up |
| `unlock/run-cross-tests.sh` + `.ps1` | Cross-host test runner (WSL clang builds, Windows runs) |
| `docs/GA107M.en.md` | Port workflow: preflight → probe → profile → experimental |
| `docs/GA107M-PERSISTENCE.en.md` | D3cold persistence analysis with source-dive receipts |
| `hold_gpu_windowed.py`, `hold_gpu_cuda.py`, `setup-hold-startup.ps1`, `setup-hold-task-at-boot.ps1` | User-startup and SYSTEM-startup GPU hold options |
| `tools/bitlocker-temporary-suspend.ps1` | Temporarily suspend BitLocker protectors for a limited reboot window (no decryption) |

### Build modes (`unlock/build-wsl.sh`, or upstream `build.ps1` with LLVM)

| Mode | What it does | Writes |
|---|---|---|
| `GA107M-PREFLIGHT` | PCI/config/BAR0/GOP inventory | **None** |
| `GA107M-PROBE` | Inventory + cold registers, real PROM + FWSEC parse for profile transcription | One config write: the PCI MEM decode bit (the same write the driver makes every boot) |
| `GA107M-EXPERIMENTAL-entryLatchedSs1Full` | Exact-card unlock; manual `U` confirmation; one transaction per boot | The transaction itself |

The experimental build **fails closed** (`#error`) until the exact-card
profile pins are transcribed from your own probe report. All upstream
fail-closed gates, one-shot confirmation, containment and halt behavior are
retained. No VBIOS/eFuse write path exists in any build.

### Workflow

1. Build + boot the preflight (read-only) — sanity and report layout.
2. Build + boot the probe — collect cold registers, PROM hash, FWSEC
   descriptor. Run `tools/ga107m-parse-preflight.py` on the report.
3. Transcribe the emitted pins into `unlock/port.h`.
4. Run the cross-host test suite including the full-transaction mock against
   **your** PROM capture.
5. Build + boot the experimental unlock; press `U` at the prompt
   (Esc or 30 s cancels). Windows boots with the override applied.
6. Hold the dGPU awake at logon to keep it; measure with a
   clock-normalized GEMM benchmark (ops/SM/clk), not raw TFLOPS.

Firmware assets are not redistributed; generate them from pinned upstream
inputs ([docs/ASSETS.en.md](docs/ASSETS.en.md)).

## Attribution and lineage

GPL-2.0-only. This fork is based on
[impacifist/Windows-GPU-UNLOCK](https://github.com/impacifist/Windows-GPU-UNLOCK),
which is based on
[rough2515-hub/rtx3080-unlock-public](https://github.com/rough2515-hub/rtx3080-unlock-public)
and [Zagitta/gpu-unlock](https://github.com/Zagitta/gpu-unlock). The GA107
laptop-FWSEC DMEM fix and GA107 firmware-path facts are from
[IHaBiS02/ga107-unlock](https://github.com/IHaBiS02/ga107-unlock) (Linux kmod
port for an LG RTX 3050). NVIDIA firmware remains under its own terms.

## License

GPL-2.0-only; EDK2 and separately attributed components retain their
licenses. See [LICENSE](LICENSE), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
and [CHANGES.md](CHANGES.md).
