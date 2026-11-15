# GA107M selector override via VBIOS: feasibility assessment

**Phase-1 conclusion (runtime override):** The linked GA107 project establishes a credible runtime VBIOS **data override** route, independent of EEPROM bootability. The supplied dump fits the reported Ampere transport limit after EFI removal and passes the independent structural checks below. The desired SS0/SS1 writes are expressible in legacy init bytecode, but no executed, authorized selector-writing hook has been identified in this image. A VBIOS-only selector unlock is therefore **not yet demonstrated**, not proven impossible. Runtime image acceptance does not by itself establish arbitrary script execution or selector-register permissions.

**Phase 2 (physical flash):** Cold-booting a modified image from EEPROM is a separate, deferred question. Flash authentication and board recovery are not prerequisites for offline Phase-1 investigation. No validated flashable selector-unlock implementation follows from the current evidence.

## Phase 1: runtime VBIOS override investigation

### What the GA107 example demonstrates

[qwecvw/rtx-3050m-vbios-override](https://github.com/qwecvw/rtx-3050m-vbios-override) reports a GA107 `10de:25a2` laptop using a RAM-loaded power/current-table override. Crucially, its README states that **all benchmarks also loaded the separate `gpu-unlock` module; the override without it was not tested**. Its edited power/current limits are not SS0/SS1 settings. Its runtime-D3 relock report also shows that this particular combination is not a VBIOS-only solution for restoring the selectors after power gating. These are upstream hardware reports, not measurements on the supplied laptop.

The example's [third-party manifest](https://github.com/qwecvw/rtx-3050m-vbios-override/blob/main/third_party/README.md) pins editor commit `cf2566d9a12aaf9ec5dec86202168723d7164d3a` and Linux loader commit `418942a5fe40269787d4b57e97ab0d2a922011cc`. The following transport and Linux staging conclusions use those source revisions rather than assuming current upstream behavior is identical.

### What the Linux loader actually substitutes

The pinned [per-GPU patch](https://github.com/Panchovix/nvidia-linux-vbios-vix-loader/blob/418942a5fe40269787d4b57e97ab0d2a922011cc/patches/615-vbios-override-per-gpu.patch) implements this path:

1. Linux probe reads the per-device binary into host registry storage.
2. `_kgspVbiosOverridePrepare()` is called from `kgspAllocBootArgsCommon()`. It checks mode/size and uses `kgspVbiosOverrideFindLegacy()` to require one matching, checksum-valid legacy image. The patch's host allocation bound is 4 MiB; **that is not the reported 512 KiB Ampere receiver bound**.
3. It copies bytes from the selected legacy image onward into per-GPU system memory. `kgspPopulateGspRmInitArgs_IMPL()` publishes that buffer's physical address and size in `vbiosOverrideArgs` for GSP boot. The log saying "staged for GSP" proves staging, not acceptance or execution of a particular table.
4. It does not interpret legacy init instructions, write SS0/SS1, change FEAT_OVR_PLM, patch the FWSEC FRTS command, or perform the unlock project's transient Booter metadata transaction.

The corresponding NVIDIA [kernel GSP source](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/615.71.09/src/nvidia/src/kernel/gpu/gsp/kernel_gsp.c), `kgspInitRm_IMPL()`, **still extracts the VBIOS from ROM separately** using `kgspExtractVbiosFromRom_HAL()` and parses FWSEC from that image. Its comments require FWSEC/FRTS before loading GSP-RM. The override patch does not redirect that extraction to the supplied runtime buffer. Thus changing the override's embedded FWSEC is not shown to replace the host's FWSEC/FRTS preparation path.

The image becomes input to GSP-RM, not a replacement for every earlier GPU firmware stage. The reviewed open code does not expose the closed GSP receiver's complete init-script policy or its ordering relative to selector initialization/permission lockdown. The driver's GSP lockdown notification concerns GSP register access and must not be equated with evidence about FEAT_OVR_PLM. Neither a blanket "no scripts execute" claim nor an assumption that all legacy scripts execute is supported.

### Supplied dump: independent in-memory transport checks

The pinned editor's [Ampere implementation](https://github.com/Panchovix/nvidia-vbios-vix-editor/blob/cf2566d9a12aaf9ec5dec86202168723d7164d3a/src/nvidia_vbios/rom/ampere_relocated.py) keeps legacy plus contiguous E0 images, removes EFI and bytes after the final E0, and preserves the bank prefix in its exported file. Unlike its Ada relocation routines, **Ampere does not move tables within an image, rewrite pointers, or require an Ada reviewed-image hash**. The loader subsequently skips the prefix. This is a runtime transport representation, not a proposed flash image.

For this dump, the relevant transformation was constructed **only in memory**, using the existing bounded parser and independently checking the result against the source recipe:

| Check | Observed result |
|---|---|
| Full original capture | 999,424 bytes; SHA-256 `5f424dea541fe13f7a70c696b3f52a4706739c5a4867195082219603a9e8595f` |
| Contiguous physical ROM-chain view | 588,288 bytes at original-file `0x9400`; too large to send unchanged under the reported 512 KiB bound |
| Removed EFI image | 99,328 bytes (`0x18400`) |
| Runtime legacy+E0 view | **488,960 bytes (`0x77600`)**, 35,328 bytes below `0x80000` |
| Runtime chain, offset/size relative to legacy | Type 0: `0 / 0xfe00`; E0: `0xfe00 / 0x5600`; E0: `0x15400 / 0x62200` |
| Runtime-view SHA-256, excluding prefix | `8c7cba14a4f84b1a635332dec5ea5dd8d7753ce453e422393497025b90ac1b74` |
| Image byte-sum checksums | All three zero; no checksum repair needed |
| Legacy selection | One 512-byte-aligned matching legacy candidate, PCI ID dword `0x25a210de`, valid checksum |
| BIT checks | One header within legacy; 91 nonzero known pointers/token data pointers checked, none beyond the runtime view |
| Unexpected aligned image signatures | None |
| Expansion bias after EFI removal | Zero |
| Init pointers/roots after EFI removal | Identical pointers; still zero discovered roots |
| Original capture after analysis | Re-read hash unchanged |

These are **partial independent transport checks**, not execution of the upstream editor's complete semantic-probe suite, a loader installation, or a hardware acceptance test. No payload file was saved. The existing inventory tool calls offsets within the physical four-image view "logical"; above and in the upstream recipe, runtime/GSP logical offsets instead skip EFI. Legacy/BIT I offsets are unchanged because they precede EFI; E0 file offsets must be mapped through the removed interval. Do not use one coordinate system as the other.

### Can SS0/SS1 be expressed through this VBIOS?

**At the instruction-format level, yes:** legacy `ZM_REG` (`0x7a`) encodes a 32-bit MMIO address and a 32-bit value. Two such writes plus DONE occupy 19 bytes and can describe SS0 `0x0082381c = 0x88888888` and optional SS1 `0x00823820 = 0x00000008`. A constructed in-memory sequence was passed through the local decoder, which recognized both selectors and DONE. This did not modify the dump and does not prove that GA107's runtime consumer supports that opcode/path. The default entry-latched unlock policy would instead preserve SS1; a two-write experiment would deliberately select the optional full policy.

**At the image/consumer level, not yet established:** the supplied BIT I script list begins with a null pointer; private, PCIe, boot-script and non-GC6 script roots are null. EFI removal does not reveal a different script list. The `0xf76`-byte Devinit tables region remains data of unverified runtime semantics, not an identified executable hook. The reviewed editor exposes power/current and related data tables, not a verified GA107 selector-init editor.

Adding a new pointer and 19 bytes is therefore not a demonstrated solution. It would assume all of the following without evidence:

- The runtime GSP/driver consumes that specific root on this GPU and driver build, even though stock leaves it empty.
- The interpreter accepts the required addresses and opcodes rather than ignoring/filtering them.
- That execution context can write the protected selectors, or reaches an authorized permission-opening path.
- The writes occur after any stock initialization that would overwrite them and early enough for the desired latch/reset behavior.

The existing unlock transaction opens access, writes/verifies selectors, performs FLR and checks the handoff; merely encoding the final values does not reproduce those prerequisites. Conversely, permission checks on host MMIO do not prove a firmware interpreter lacks its own authorized access. That must be established for the actual consumer. Modified power data being accepted does not settle it either way. EEPROM signature acceptance is **not** the blocker for this Phase-1 conclusion.

### Windows transport and unchanged security policy

Current upstream also offers a [Windows runtime loader](https://github.com/Panchovix/nvidia-windows-vbios-vix-loader). Its documented mechanism writes the chain to the NVIDIA driver's per-adapter `vbios` registry value and applies it at Windows restart; its `verify` operation compares driver-reported VBIOS bytes through NvAPI. Recent versions leave the driver's default firmware mode unchanged, while older versions forced GSP mode. Its README reports Ampere data overrides, including an RTX 3050 6GB Laptop GPU, and lists supported driver versions. This is additional transport evidence, **not** an SS0/SS1 result or proof that Windows and Linux execute the same init scripts.

Unlike installing a locally rebuilt Linux module, this documented Windows registry route does not call for a new kernel-module signing key, Secure Boot disablement, or trust enrollment. No registry/driver/boot changes have been made here, and current driver compatibility, Secure Boot behavior and BitLocker/recovery behavior on this laptop have not been tested or guaranteed. The Linux rebuilt-module path must not be presented as compatible with the user's no-new-trust requirement without checking the system's existing trust. No power/current/voltage increases are part of this selector investigation.

## Follow-up: runtime consumer and test boundaries

### Runtime consumer and permissions: follow-up source trace

**Target separation:** The supplied GA107 `10de:25a2` dump belongs to the user's personal laptop, where the user has administrator rights. The analysis workstation has an RTX 2000 Ada Generation Laptop GPU (`10de:28b8`) and no administrator rights. Its driver version and Ada compatibility are not prerequisites for offline GA107 research. No payload, register setting or elevated test is to be applied to that workstation.

**Personal-laptop baseline supplied by the user (2026-11-14):**

| Field | Reported value |
|---|---|
| Manufacturer / model | GPU Company / GGNC51518 |
| NVIDIA GPU | GeForce RTX 3050 Laptop GPU, `10de:25a2`, subsystem `11401d05`, revision A1 |
| NVIDIA driver | 552.22; Windows version `31.0.15.5222`; signed package, provider NVIDIA |
| VBIOS | `94.07.3b.00.ea` |
| Integrated GPU | Intel UHD Graphics, `8086:9a68`, driver `31.0.101.4502` |
| Windows | Build `26300.9974`, DisplayVersion `26H2`; registry ProductName reports `Windows 10 Home` |

The legacy ProductName string and build/display-version labels must not be used to assert the exact Windows product/channel without corroboration. The presence of an Intel adapter does not prove which GPU drives each display or establish a usable recovery route. Driver provider/signature status does not identify whether the installer came from NVIDIA, the OEM, or Windows Update.

**Initial compatibility boundary:** 552.22 is below the reviewed loader's `MIN_DRIVER = (616, 56)`. Its `load` function refuses this version **including dry runs**, before opening the writable driver key. This is a verified loader gate, not proof that 552.22 has no related internal override mechanism. Do not remove the check, force GSP mode, or write the payload manually as a workaround.

**Subsequent user update:** The user installed 621.01 from the official NVIDIA notebook package and supplied driver output reporting 621.01 with the same device/subsystem IDs and a VBIOS string consistent with the earlier capture. Restart was subsequently acknowledged; the manually transcribed output is not a byte-exact installed-file inventory. This clears the loader version gate, not its payload validation or the missing selector execution proof. Graphics-switch availability, an independently verified display recovery route, and permission for an additional properly signed kernel driver remain unestablished. Secure Boot/BitLocker and no-new-trust constraints remain unchanged.

The Windows loader was reviewed at commit [`6f944f561bdc585593667eb1146e1393ca579580`](https://github.com/Panchovix/nvidia-windows-vbios-vix-loader/tree/6f944f561bdc585593667eb1146e1393ca579580). Its [operations](https://github.com/Panchovix/nvidia-windows-vbios-vix-loader/blob/6f944f561bdc585593667eb1146e1393ca579580/src/operations.rs) store the payload in `vbios` (`REG_BINARY`), with readback of registry changes. Its [NvAPI wrapper](https://github.com/Panchovix/nvidia-windows-vbios-vix-loader/blob/6f944f561bdc585593667eb1146e1393ca579580/src/nvapi.rs) calls `NvAPI_GPU_GetVbiosImage` (interface ID `0xfc13ee11`); verification compares two driver-returned images with the registry payload. Neither file implements a Devinit interpreter, supplies a selector-access allowlist, or opens FEAT_OVR permissions. In the documented default mode, the driver reads the override itself; that does **not** mean the GPU has no firmware involvement. The proprietary receiver's script dispatch and register-access policy are not exposed by this loader.

An architecture-specific cross-check of Nouveau is important because its generic interpreter is not automatically GA107's boot consumer:

| Source path / symbol | Verified behavior | Evidence boundary |
|---|---|---|
| [GA107 device mapping](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/engine/device/base.c), `nv177_chipset` | Selects `ga100_devinit_new`. | Nouveau implementation, not NVIDIA Windows RM. |
| [GA100 Devinit](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/ga100.c), `ga100_devinit` | Uses `tu102_devinit_post`; selects `r535_devinit_new` when GSP-RM is active. | Shared implementation name does not make GA100 and GA107 override formats interchangeable. |
| [TU102 Devinit](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/tu102.c), `tu102_devinit_post` | Waits for bit 0 at `0x118128` and low byte `0xff` at `0x118234`; does not call `nvbios_post` there. | These are completion checks, not a feature-selector permission grant or an identified firmware interpreter. |
| [GSP wrapper](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/r535.c), `r535_devinit_new` | Builds a function table retaining the hardware `post`/`disable` callbacks, not its `init` callback. | Does not reveal the closed GSP script consumer. |
| [NV50 initialization](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/nv50.c), `nv50_devinit_init` | Can dispatch a display encoder's `info.script[0]` when its post condition holds. | Display scripts are a separate route; this is not proof of a Windows runtime hook or selector access. Empty BIT I roots are not an exhaustive inventory of all scripts. |
| [GM200 Devinit](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/gm200.c), `gm200_devinit_post` | Loads VBIOS application type `0x04` on PMU, uploads BIT I tables at `+0x14/+0x16` and scripts at `+0x18/+0x1a`, then starts DEVINIT. | Concrete **Maxwell** metadata consumer, not evidence that GA107's runtime override invokes this path. |
| [Generic interpreter](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/subdev/bios/init.c), `init_zm_reg` → `init_wr32` → `init_nvreg` | Applies execution/address handling, then passes the address through `nvkm_devinit_mmio`; writes only if the resulting address is not `~0`. | Encoding a register write is not proof of access to protected hardware. |
| [Devinit base](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/base.c), `nvkm_devinit_mmio` / `nvkm_devinit_fini` | Uses an optional chip MMIO callback; marks `post = true` for non-poweroff suspend states. | No callback is not a hardware privilege grant. The code also contradicts a blanket claim that Devinit can only run once at boot; actual resume behavior is path-specific. |

These Nouveau links refer to the upstream source reviewed during this investigation, not a tested Windows driver implementation. The trace identifies useful boundaries, **not the exact closed Windows GA107 interpreter**. There is still no verified dispatch from the registry override to a selector-writing hook, no established execution privilege for that hook, and no established ordering relative to stock selector initialization. A null non-GC6 pointer does not prove that the GPU has no resume initialization. The first legacy script-list entry is null; the list pointer itself is **nonzero** (`0x3f4f`).

The known unlock implementation provides the relevant permission counterexample: its transient firmware transaction precedes host selector writes and checked reset/handoff. A generic script opcode does not reproduce that transaction. Conversely, its host-access requirements cannot establish what an authenticated firmware consumer is allowed to do. Neither direction should be assumed.

### 621.01 offline binary trace: image staging to format parsing

The exact [official notebook installer](https://us.download.nvidia.com/Windows/621.01/621.01-notebook-win10-win11-64bit-international-dch-whql.exe) was downloaded to an analysis cache outside this repository. Windows Authenticode reported **Valid**, signer NVIDIA Corporation, for both the installer and extracted display-driver binary. Extraction used the existing Windows archive reader; **the installer and extracted driver were not executed or installed on the analysis workstation**. No driver binary was modified. Hashes, sizes, target INF matches and trace coordinates are recorded in [the evidence manifest](../evidence/windows-driver-621.01.json).

The package's target INF lists `PCI\VEN_10DE&DEV_25A2&SUBSYS_11401D05` in two OS-specific model sections, using installation sections 131/132. This proves an exact hardware-ID entry exists in the package, not which installation section Windows selected. The package version is `32.0.16.2101`. The previously supplied user output now reports NVIDIA version 621.01.

All addresses below are **RVAs**, not runtime absolute addresses or patch locations, and apply only to the 102,231,272-byte driver with SHA-256 `3f8d33495cabeb2fa11ebdcf0b12705e9ccfb4e53005324bcd86e9dea8ba2ab4` (preferred image base `0x140000000`). Private NVIDIA symbol names have not been recovered; helper roles below are inferred from decoded argument and buffer flow.

| Static observation | Evidence / boundary |
|---|---|
| `vbios` string | Null-terminated ASCII at file offset `0xd5d730`, RVA `0xd5df30`; several instruction-aligned RIP-relative references, not just a loose string hit. |
| Size and buffer reads | Within runtime fragment `0x1864e0..0x18708c`, the calls at `0x186a60` and `0x186aad` pass the `vbios` name to helpers `0xf3060` and `0xf2f30`. Output size controls allocation and the subsequent buffer-read argument. Helper decoding shows wide-name construction and a shared callback/query path. The exact final Windows registry API and all branch conditions are not yet resolved. |
| Publication to BIOS object | The successful-read path converges on stores of the buffer and size at object offsets `+0x2b8` / `+0x2c0` and the call at `0x186e3a` to `0x18a3b0`. Those are private structure offsets, not GPU registers. |
| Parsing of supplied image | Entry `0x18a3b0` selects the object buffer when its Boolean argument is set. Its continuation at `0x18a43c` checks the option-ROM signatures `0xaa55`, `0x4e56`, `0xbb77`, then header signatures `PCIR`, `NPDS`, `RGIS`. This identifies image-format parsing, **not an init-opcode interpreter**. |
| BIT discovery | The routine starting at `0x184ef0`, with continuation `0x184f22`, scans for `0xb8ff` followed by `BIT\0` and calls `0x5fbae0`. That helper accumulates header bytes according to its length field and tests the low-byte sum. This is metadata discovery/checksum handling, not execution of BIT I. |
| Nearby initialization settings | Separate decoded references to `RMExecuteDevinitOnPmu` and `RMDevinitBySecureBoot` exist. Their presence does not establish applicability to GA107, dispatch from the substituted buffer, or permission to write selectors. No such setting was changed. |

Static dispatch from parsed metadata to the next stage is now bounded to concrete RVAs in the same 621.01 image. After image parsing and object-field setup, fragment `0x1864e0..0x18708c` calls `0x5fe760` at `0x186f5d` and `0x185030` at `0x186f76`. The second routine calls `0x184ef0` (BIT scan) and a dependent stage at `0x5fbb60`, then continues through additional helper clusters in the `0x18c***` range. This is evidence that parsed data is consumed by a multi-stage host dispatch path, not an isolated one-off parse.

A focused follow-up on that chain found the first **MMIO-capable writer candidate branch** at `0x18fdce` / `0x18fde5`, dispatching to `0x5f1780` or `0x5f0ad0` depending on state byte `+0x1634`. On the `0x5f1780` branch, helper `0x5f15b0` reaches an indirect execute-like leaf call at `0x5f16e7` (`call qword ptr [rip + 0x86c3bb]`) after preparing parsed offsets and command metadata. This remains a static candidate, not a proven selector writer.

The same pass ties PMU/SecureBoot policy reads to the candidate branch through concrete predicates. `RMExecuteDevinitOnPmu` (`0x18a038`) and `RMDevinitBySecureBoot` (`0x18a122`) update state bytes including `+0x298` and `+0x2a7`; later branch checks require `byte[+0x298] != 0` (`0x18fda1`) and `byte[+0x2a7] == 0` (`0x18fbc7`) before reaching `0x18fdce` / `0x18fde5`. This is static gating evidence for entry into the writer-candidate branch, not proof that selector addresses are written.

A deeper trace of the two branch targets resolved **what kind of writer each is**, and reached a hard static boundary. State byte `+0x1634` selects the branch: nonzero dispatches `0x5f1780`, zero dispatches `0x5f0ad0`. Branch `0x5f1780` both parses the override buffer into object fields `+0x15xx`/`+0x16xx` and performs engine operations through **13 CFG-guarded indirect calls**; branch `0x5f0ad0` only populates fields `+0x1578..+0x15d8` via the byte extractors (`0x18c6e0` to `0x5e5420`) and `0x1c09d0`, with no HAL execute dispatch on its own frames. So the execute branch is the one gated by the PMU/SecureBoot bits; the alternate is a parse-only variant.

The execute branch's "write" sites are **not direct instructions**. Each `call qword ptr [rip + ...]` on that path resolves to a single slot (`0xe5daa8`) pointing at the Control-Flow-Guard dispatch thunk `0xd46290` (`jmp rax`). The real targets are runtime-populated HAL function pointers loaded from object offsets (`+0x158`, `+0x160`, `+0x198`, `+0x1a8`, `+0x1d8`, `+0x320`, `+0x638`, `+0x640`, `+0xbc8`, `+0x750`). Register addressing is further abstracted: the aperture base lookup at `0x1c0db0` and the address computation at `0x1c0a20` map a register through a BAR-aperture layer rather than storing a literal selector address. **The concrete selector-writing instruction is therefore behind runtime HAL dispatch plus aperture mapping and cannot be named from this static image alone.**

Register-permission and reset/latch boundaries remain unresolved. The trace identifies policy reads (`RMExecuteDevinitOnPmu`, `RMDevinitBySecureBoot`) and internal state bytes/words, but no decoded, authenticated writer to the GA107 selector addresses and no proven FEAT_OVR privilege transition. It also does not prove where Windows re-runs this chain on reset/resume versus cold start. The nearby FLR timeout key (`RMPcieFlrDevinitTimeout`) suggests a reset-associated path exists, but does not by itself establish selector-latch timing.

The binary also contains alternate paths, including a separate use of the name `vbios` as a DWORD setting. String identity alone must not conflate those paths with the binary image read. MSVC's exception directory splits several routines into runtime-function **fragments**; decoding only the first entry can miss a fall-through continuation. No guessed fragment boundary or mid-instruction decode is used as execution evidence.

Reproducibility: [the read-only inspector](../tools/inspect_windows_driver.py) accepts an extracted AMD64 PE image and optional `--fragment` RVAs. It emits JSON on stdout with full-file hash, exact string offsets, candidate RIP-relative LEA references, entry-alignment checks and selected fragment disassembly. It does not import/load the target binary, access a device, edit a payload, open registry keys or write output files. It requires separately available LIEF/Capstone analysis libraries, not runtime port dependencies. Eight [synthetic tests](../tools/test_inspect_windows_driver.py) exercise literal search, signed displacement calculation and fragment boundaries. LIEF 1.0.0 and user-approved conda-forge Capstone 5.0.9 were used locally; no existing packages were replaced. WSL could not start under workstation logon policy; that policy was left untouched.

**Port status:** This advances the trace from a closed receiver of unknown location to specific host buffer-handling and format-parsing code. It does **not** yet supply a Windows selector writer, an authenticated firmware execution path, or a VBIOS patch. The remaining substantive work is to follow the parsed initialization data and architecture dispatch to its execution agent, then establish register filtering, hardware privilege and latch/reset timing. No selector literal match was found in the six inspected host/firmware files, but computed addresses, packed images and other consumers remain possible. No public release payload or runtime hook is justified by this trace alone. The driver image was not matched byte-for-byte against the personal laptop's installed driver.

### Direct question: does the registry method permit SS0/SS1 changes?

**Current answer: no direct register-write facility is implemented by the reviewed loader; an indirect VBIOS-script route remains unproven, not ruled out.** The registry payload is an image buffer, not an address/value command list. Adding registry values named `SS0`, `SS1`, or after their MMIO addresses has no established consumer and is not an implementation.

A targeted follow-up found these additional distinctions:

- [Windows loader issue 10](https://github.com/Panchovix/nvidia-windows-vbios-vix-loader/issues/10) reports an RTX 3050 Ti Laptop (`25a0`, VBIOS `94.07.2a.00.f4`, driver 621.01). Its [follow-up](https://github.com/Panchovix/nvidia-windows-vbios-vix-loader/issues/10#issuecomment-6529066559) says the generated Ampere payload worked for a 75 W to 100 W edit and links benchmark results. This is a user-reported **power-table** result, not a measured selector unlock, and not approval of those limits for the supplied `25a2` laptop.
- The loader's [issue 4 response](https://github.com/Panchovix/nvidia-windows-vbios-vix-loader/issues/4#issuecomment-6301711938) explicitly distinguishes runtime overrides from earlier boot initialization for Turing ReBAR. That is a useful counterexample to “the override replaces all boot behavior,” **not proof** that GA107 selectors have the same timing or restrictions.
- NVIDIA's pinned [public graphics control header](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/615.71.09/src/common/sdk/nvidia/inc/ctrl/ctrl2080/ctrl2080gr.h#L1570) defines `NV2080_CTRL_CMD_GR_GET_SM_ISSUE_RATE_MODIFIER` (`0x20801230`) with output speed selectors. No corresponding `NV2080_CTRL_CMD_GR_SET_SM_ISSUE_RATE...` declaration appears in that header. This is not a public SET API and does not establish a Windows registry control for those fields.
- The pinned [open-module build configuration](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/615.71.09/src/nvidia/generated/rmconfig.h#L311) sets `RMCFG_FEATURE_DEVINIT_SCRIPT` to zero (comment: VBIOS scripting engine for sharing register sequences). That limits what can be learned from **this open host build**; it does not prove that the proprietary Windows host RM or GSP lacks a scripting engine.
- The [GSP argument definition](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/615.71.09/src/nvidia/inc/kernel/gpu/gsp/gsp_init_args.h#L104-L108) exposes only physical address and size for `vbiosOverrideArgs`. It does not expose a caller-selected execution privilege, register allowlist, or entry point. The previously traced Linux staging patch is not a Windows receiver implementation.

No reviewed selector-specific source or hardware report connects the Windows registry override to a successful write at `0x0082381c`/`0x00823820`. Searches returning no match are not an exhaustive proof of absence. There is also no verified selector-register permission mask for the closed interpreter in these sources. The existing transaction's host PLM observations cannot be used to invent one.

**What would settle the indirect route:** Trace a particular Windows driver build from its `vbios` registry read to the image parser, identify a script/table dispatch that uses the substituted buffer (not physical ROM or an earlier cached copy), and establish the dispatch's MMIO validation, execution agent/privilege and latch/reset timing. A diagnostic result must then distinguish image acceptance from hook execution and actual selector change. Merely finding `ZM_REG` bytes, changing a pointer, or matching NvAPI image bytes cannot answer those questions. This requires further driver/firmware analysis or controlled target-hardware evidence; it cannot be resolved from the public loader's registry-writing code alone. The 621.01 binary trace above reaches image parsing but not an initialization interpreter. No target hardware selector experiment has been performed here.

### Conditional test plan for the personal GA107 laptop

This is a **transport-control plan, not an available speed-unlock recipe**. Administrator rights enable the registry write but do not grant an arbitrary VBIOS interpreter GPU privileges. The loader itself does not change clocks, power limits or tensor issue rate.

1. **Establish the target baseline.** On the personal laptop, record the GPU PCI/subsystem IDs, VBIOS and driver version, current power limits, and repeatable BF16/FP16 and FP32 workload results. Keep AC power, workload, clocks/power policy and thermal conditions comparable. Record Secure Boot/BitLocker status without disabling, suspending or enrolling anything; keep recovery information available privately, never in chat. Stop if proceeding would require a trust/security-policy change.
2. **Check the supported software before installing anything.** The reviewed loader documents Windows 10/11 x64 and NVIDIA branches 616.x/617.x/621.x, minimum 616.56. Its default-mode hardware results use 621.01; NvAPI image verification is reported on 617.42 and 621.01. A minimum version check is not a guarantee of working image verification or a recommendation to install a particular driver. Do not force GSP mode or remove an existing firmware-mode setting for this experiment.
3. **Prepare only an unmodified-data control first.** Preserve the full original capture and digest. Use the reviewed editor's Ampere normalization/validation path, selecting `ampere-relocated-v1`; do not hand-strip the NVGI prefix or submit the original four-image view unchanged. The earlier in-memory 488,960-byte view is an independent structural result, not a saved or fully upstream-validated payload. Compare all retained image bytes and table semantics, and require matching target identity and valid checksums. No SS0/SS1, power, current, voltage or clock edits belong in this control.
4. **Review load and recovery before committing.** Obtain official upstream release assets and compare their published hashes; a hash match is provenance checking, not a safety guarantee. Use the command-line `status` and `load --dry-run` workflow to inspect selection and proposed changes. Record the exact target driver key and preserve any pre-existing override separately: `load` replaces `vbios` and removes `romOverride00`–`romOverrideFF`; `unload` does not recover another tool's previous override. Confirm an independently usable display/recovery path before any load; an iGPU's presence alone does not establish one.
5. **Only then authorize a transport test on that laptop.** Load the validated control, perform a full Windows **Restart**, and run `verify`. A GPU-only restart or Fast Startup shutdown is not the documented apply/remove mechanism. A matching driver-reported image establishes transport acceptance, not selector execution. An unmodified-data control should not be expected to improve performance. Roll back unexpected behavior rather than stacking further changes.
6. **Keep the selector experiment gated on new evidence.** Do not insert the synthetic 19-byte sequence yet. First identify a consumed hook, the address/opcode policy, execution privilege, and timing/reset requirements. Start with the project's SS0-only, SS1-preserving policy if a justified candidate becomes possible. Test without the separate unlock active, so it cannot produce a false success. Require selector readback through a justified diagnostic path plus repeatable throughput improvement; compare FP32 and tensor workloads and log power/clocks to distinguish a rate unlock from ordinary boost variation. D3cold restoration remains a later, separate check.
7. **Rollback is remove plus restart.** The loader's **Remove override** / `unload` restores its saved firmware-mode value and deletes its override, followed by Windows Restart. Upstream documents Safe Mode removal if normal Windows is unusable, but this laptop's recovery path is not verified and BitLocker recovery may be requested; no promise of one-time-only recovery prompts is possible. Do not rely on forced shutdowns or guaranteed fallback to stock firmware as the recovery plan.

**Practical result:** This method makes testing accepted VBIOS **data** changes easier and avoids EEPROM writes. It has not yet made the protected GA107 selector modification an implementable VBIOS edit. Raising power/current limits is a separate experiment and is not recommended here as a substitute for the requested tensor-rate unlock. The exact 621.01 package is now available and initial host-buffer parsing has been traced; further work must establish its architecture-specific execution dispatch and privilege rather than request the same package again. The public loader alone cannot settle those questions.

### Current Phase-1 decision

**Transport feasibility: supported by source and the dump's offline structure. Selector-unlock execution: unresolved. Physical flash: deferred.** There is no evidence-based `unlocked_` image to generate yet, but no need to prove EEPROM bootability before continuing.

The next substantive research target is the exact runtime receiver/interpreter for the chosen driver: establish a consumed script/table hook, its register-access policy and its position relative to FEAT_OVR initialization. In a separately authorized hardware phase, first test an **unmodified** normalized payload and verify driver acceptance; that is only a transport control. Then test a selector candidate only after identifying its consumer, without the separate unlock masking the result. Evidence must distinguish stored/staged bytes, driver-accepted bytes, selector readback, required reset/latch behavior and workload throughput. D3cold reapplication is a later retention check, not a condition for the first runtime proof. No EEPROM operation is needed for these Phase-1 questions.

## Observed transaction and storage boundary

| Stage | Source and observation | Stored on card? |
|---|---|---|
| Identity | `unlock/app.c` `PromCheck` reads 1 MiB from BAR0 + `0x300000`, hashes it, and parses the FWSEC descriptor; probe/preflight can dump `PROM.BIN`. | Read only. |
| Setup | `unlock/vendor/unlock_610.c` `unlock_transaction_fwsec` copies the ROM FWSEC image into host DMA RAM, patches the FRTS command in that **copy**, and selects a signature using the hardware fuse version. | No ROM write. |
| Permission | `unlock_transaction_signature` publishes a V67 signature in transient boot metadata, executes the SEC2 Booter, and restores the stock signature. The firmware path opens the protected feature-register access; it does not install a new ROM image. | Volatile execution/metadata. |
| Rate | `unlock_transaction_selector` writes BAR0 MMIO `0x0082381c` (SS0) = `0x88888888`; the default GA107M entry-latched policy preserves SS1, and the optional full policy writes `0x00823820` (SS1) = `0x00000008`. It verifies readback. | Volatile selector registers. |
| Handoff | `unlock_transaction_reset` executes PCIe FLR and checks the selectors still match before letting the stock driver bind. | No flash operation. |

The feature PLM register is `0x00823804`; it is checked before the selector writes, rather than treated as a VBIOS setting. Other FWSEC, DMA, PCI configuration and Falcon control writes are transaction setup, not an alternative persistent selector storage location. This report does not imply the selector addresses are byte offsets within the ROM.

## Where the defaults come from: established versus unknown

The ROM **does** contain an FWSEC-FRTS image located via a BIT `p` token, a firmware-table entry of type `0x85`, and a v3 descriptor with signatures (`unlock/vendor/vbios_610.c`). The UEFI application also loads pinned Booter/GSP assets from files, not from an editable VBIOS command table. The project's Windows driver investigation (`GA107M-PERSISTENCE.en.md`) reports no selector-setting path in the open kernel modules; a GET-only issue-rate interface obtains data from GSP-RM. These observations distinguish ROM-resident, signed executable firmware from ordinary option-ROM metadata. Parsing an FWSEC descriptor is **not** evidence that an unsigned ROM init script owns SS0/SS1.

On the tested HP GA107M, cold-entry SS0 changes between boots; RUN03–RUN05 verify post-FLR overrides, and RUN04 with a held-awake GPU verifies improved Windows throughput. The same notes attribute later loss to a D3cold power cycle and fuse-driven initialization. This supports a **power-cycle reinitialization** model; it does not identify whether the decisive write is made by ROM firmware, fuses/straps, or a closed GSP/PMU stage. An unsigned, programmable VBIOS table for these selectors has not been found. A different VBIOS image, or a ROM modified without passing firmware authentication, is not a demonstrated solution. Changing eFuse defaults is not a VBIOS flash.

**Conditional feasibility:** A persistent VBIOS solution would need an authenticated, board-compatible initialization path that re-applies both the desired selector policy and the required permission state on **every** relevant GPU power-on, including Windows runtime D3cold exits. Neither the existence of such a writable path nor its authorization on this laptop has been shown. The risks include rejecting the image during boot, losing display/GPU access, and an unrecoverable laptop GPU without external recovery equipment. No flash is recommended on current evidence.

## Read-only ROM comparison

`tools/compare_vbios.py` accepts a locally supplied original PROM dump or the supported NVGI/RFRD container; an optional second file is compared by full-file size, SHA-256, PCI option-ROM image ranges, FWSEC structure digests, and bounded byte-difference ranges. It prints JSON to standard output and never writes a file or accesses PCI/MMIO. It limits input to 1 MiB and rejects unsupported or malformed layouts. The optional `--expected-sha256` checks the full original capture, not the derived ROM view. Use a digest for the same board and capture representation; the project's HP exact-card pin is not automatically applicable to the user's dump. Even a match is not authentication. The supplied NVFlash capture is assessed below without distributing its contents as a test fixture.

This utility does not disassemble initialization bytecode, verify cryptographic signatures, establish the presence of selector writes, generate a patched ROM, or confirm that a candidate will boot or survive D3cold. Different hashes locate differences; they do not prove a candidate is flashable or functional. The next defensible step is to compare *read-only* original captures across the same board/firmware and establish the actual cold-start and D3cold writer before considering firmware changes.

## Selector reference scan

`tools/vbios_devinit.py` reads one locally supplied PROM dump and reports every 32-bit little-endian word in it that falls inside the feature-selector window (default `0x00823800` plus `0x40`, covering the feature PLM, SS0 and SS1). Each hit records its file offset, alignment, surrounding bytes, containing option-ROM image, and membership in parsed FWSEC structures. Those structures do not establish complete cryptographic coverage; bytes outside them are not proven unsigned or editable. Legacy `signed_region` and raw-ROM `editable_candidate` fields are location hints only. NVGI hits are never marked editable candidates. The tool also enumerates the BIT token table and notes whether an init (`I`) token is present at all; token pointers remain logical ROM addresses.

This is a byte search, not a decoder. It does not interpret init bytecode, verify signatures, establish that a located word is an executable register write, or show which stage programs the selectors at power-on. Zero hits do not exclude computed, encoded or compressed references. Non-zero hits likewise do not prove an executable initialization path.

## Supplied NVFlash dump: verified offline analysis

The supplied `RTX3050M.rom` is 999,424 bytes, with full-file SHA-256 `5f424dea541fe13f7a70c696b3f52a4706739c5a4867195082219603a9e8595f`. It starts with `NVGI`, rather than a PCI option-ROM header. An `RFRD` v3 record at file offset `0x5000` has a 40-byte header, points to `0x9400`, and supplies length `0x8fa00`. The parser cross-checks this range against a complete four-image ROM chain (types `0`, `3`, `0xe0`, `0xe0`) before using it as a bounded analysis view. This is narrow support for the observed layout, not a complete NVGI specification. Other container components remain opaque, and unsupported or ambiguous records fail closed.

The derived view SHA-256 is `e173c07ebe06aed495e7786edaddd96db1d9ea2fcb6fb71c2693813d482ee9db`; it must not be confused with the full capture digest or another board's PROM pin. The FWSEC descriptor, image and signature-block geometry parse successfully, and a BIT init (`I`) token is present. The full-file selector-window scan finds **zero literal address matches**. This does not identify the stock selector writer or establish whether a persistent override is possible.

Both inventory and scan now accept this dump directly. Reported region offsets refer to the original file, with separate logical offsets for the ROM view. Reads, comparisons and vault hashes retain the exact full capture bytes; no extraction file or modified ROM is produced by analysis. The experimental patch generator explicitly rejects NVGI inputs. Offline validation passed all 50 Python tool tests (36 VBIOS tests, including eight new container tests), and re-reading the supplied capture confirmed its full SHA-256 was unchanged.

## Initialization bytecode decoder

`tools/vbios_init.py` accepts the supported NVGI container or a raw ROM and prints JSON only. From the project directory, `python tools/vbios_init.py ../RTX3050M.rom` analyzes the supplied dump. `--script 0xOFFSET` adds an explicitly supplied **logical ROM** root; such a root does not prove that firmware executes it. `--max-instructions` controls the global instruction budget (default 4096, maximum 16384). There is deliberately no ROM output, patch, or flash option.

The decoder reads BIT `I` v1 metadata and discovers the legacy main script list, private boot script and PCIe settings script. It inventories Devinit table/script ranges separately from code roots, including non-GC6 script metadata and the NVLink configuration pointer. Its static graph follows direct calls, indexed subscript references, conditional-jump alternatives and fallthrough; it stops at DONE or any unsupported/malformed instruction. Cycles are deduplicated, operand overlaps are reported, and bytecode offsets are restricted to the legacy 16-bit address space. Scripts, instructions and emitted MMIO records are resource-bounded.

Supported MMIO operations include direct/16-bit constants, masks, macro constants, I/O-selected alternatives, sequential addresses, repeated writes to one register, register copies, masked adds and symbolic table translations. This can identify a selector write whose address is computed from a sequence base rather than stored literally. Value locations distinguish original-file and logical-ROM offsets, but are **not approved patch locations**. Display-context addresses remain unresolved rather than guessing head/OR/link values. Some port/I2C instructions are framed only; their writes are not included in the MMIO count. Conditions are not evaluated and loops are not executed. Unknown opcodes are never skipped to hunt for plausible instruction bytes.

Format references are NVIDIA's [BIT specification](https://download.nvidia.com/open-gpu-doc/BIOS-Information-Table/1/BIOS-Information-Table.html), Nouveau's [initialization interpreter](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/subdev/bios/init.c) and [GM200 Devinit metadata consumer](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/gm200.c). Using those formats does not establish how the supplied Ampere firmware executes or authenticates them.

### Result on the supplied capture

| Item | Verified value |
|---|---|
| BIT I version / length | 1 / 36 bytes |
| BIT I location | Logical `0x299`, original file `0x9699` |
| Main script table | Logical `0x3f4f`, original file `0xd34f`; first pointer is zero |
| Private boot / PCIe settings script | Both null |
| Devinit tables range | Logical `0x3efb`, original file `0xd2fb`, size `0xf76` (3958 bytes); contents remain opaque |
| Boot scripts / non-GC6 scripts ranges | Both null |
| NVLink configuration pointer | Logical `0x7d4f`; not treated as an initialization script |
| Discovered code roots / decoded instructions | 0 / 0 |
| Patch eligibility | False; authentication and execution remain unknown |

The nonzero NVLink pointer is not a hidden selector-unlock script. A nonempty tables region is not by itself a code entry point. Empty legacy roots do not rule out firmware machine code, other script consumers, or a stock initialization writer elsewhere. No `unlocked_` ROM was generated: neither a valid selector-writing path nor a permissible authenticated modification has been established. A lasting override would also have to restore register access and selectors on the relevant power transitions, not merely store different bytes in flash.

Validation: all **73** offline Python tool tests passed, including **23** decoder tests for instruction formats, computed sequence addresses, masks, table references, cycles, bounds, opaque metadata, unsupported opcodes, CLI behavior and NVGI offset mapping. The real dump was analyzed through the CLI; its full-file SHA-256 remained `5f424dea541fe13f7a70c696b3f52a4706739c5a4867195082219603a9e8595f`. No hardware access, flash, trust enrollment or BitLocker change was performed.

## Original-image custody

`tools/vbios_vault.py` keeps the unmodified VBIOS. `store` accepts two or more independently taken captures, requires them to agree byte for byte, validates the ROM structure, checks the digest against the exact-card pin read from `unlock/port.h`, and writes redundant copies with a digest-protected manifest. `verify` rechecks every copy and refuses a vault whose metadata changed after storing. `restore` verifies first and then names the copy to flash.

The tool never touches PCI or MMIO and never flashes. Multiple paths with identical contents do not prove independently taken captures. A verified vault establishes byte custody, not recoverability: restoring still requires a working recovery path. The user's precise laptop configuration and ability to boot with an unresponsive dGPU have not been verified.

## Gated patch generation

`tools/vbios_patch.py` is an experimental raw-ROM byte editor, not a validated selector-persistence implementation. It rejects NVGI containers, requires a verified vault whose full-file digest equals the input, and checks expected bytes and overlap with parsed FWSEC structures. These checks do not prove authentication coverage, execution semantics, recoverability, or flashability. The legacy scanner candidate gate is insufficient to establish any of those properties. It was not used to modify the supplied capture.

After applying the edits the tool restores only the checksums the original actually held: an option ROM image that summed to zero is brought back to zero through a pad byte, and an image that did not is reported and left alone rather than given an invented checksum. The pad byte may not fall in a signed region or in an edit. The result is re-parsed, the option ROM geometry and the FWSEC descriptor, image and signature digests must match the original byte for byte, and a bounded difference report is printed. Output is written only to a path that does not exist.

The tool never accesses PCI or MMIO and never flashes. It does not decode init bytecode, verify a signature, or show that an edited word is executed at power-on. Editing an address literal changes where a write lands, not the value written. A generated image is a candidate for analysis; it is not evidence that the card will accept it, boot with it, or keep the selector policy across a D3cold exit.
## Phase gate

For Phase 1, continue with runtime-consumer identification and payload-acceptance evidence; do not make EEPROM flashability a prerequisite. A scan or verified vault alone does not establish a selector-execution path. For Phase 2, physical-image authentication, board compatibility and recovery remain separate unresolved requirements. No modified user image, registry change, driver installation or flash was performed during this investigation. A zero-literal result is inconclusive, not proof that no firmware path exists.