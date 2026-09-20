# Improvement roadmap

Grounded in the working tree inspected on 2026-09-19 (America/Los_Angeles). This is a plan; no product changes or hardware claims were made by this ideation pass.

The project already demonstrates bounded Python/MATLAB translation, independent development/audit scoring, exact-cycle RTL simulation, Xilinx mapping, mapped replay, SHA chaining, SLAM cosimulation and a complete legacy Wi-Fi receiver with recorded RTL and mapped verification. These are the baseline to preserve.

No applicable AGENTS.md or existing beads workspace was found. Both open and closed issue inventories were empty after initialization. Existing modified and untracked Wi-Fi work was treated as the current design, not as missing functionality.

Start with evidence binding, durable resume, build reuse, consistent resource gates and contract preflight. They make existing workflows trustworthy and easier to use before expanding the research surface.

Backlog: `fpga-2hx`. All issue changes were made with `br`; the exported backlog is [../.beads/issues.jsonl](../.beads/issues.jsonl). Each feature has separate core, integration, unit-test and real-E2E tasks. Parent-child links organize work; explicit blocking edges identify required deliverables.

## The strongest five

### 1. Bind every verification claim to an immutable artifact bundle — `fpga-2hx.1`

**Observed:** fpga_lab/translate.py writes a source/vector manifest; vhdl.py results identify VHDL but not the complete mapped artifact chain. cosim.build_server checks proposal VHDL and only checks the parent summary if present; it hashes mapped.v after copying rather than comparing it with a verification-time digest. wifi_generate.combined_receiver checks accepted and VHDL hash but not the full verification scope. The current Wi-Fi result records accepted=true with RTL and mapped verification complete; its results JSON, coverage matrix, README and demo report are separate reporting surfaces that should be derived from the same bound evidence.

**Why this is worth building:** The project sells evidence of correctness. A user must be able to tell which exact source, netlist, tests and target justify a claim, even after moving final/ or rebuilding an image. Today partial provenance and independently edited reports make that answer harder than necessary. A checked bundle makes sharing a result, starting SLAM, and composing receiver blocks dependable, while a generated summary prevents stale documentation from understating or overstating success. This ranks first because it protects every downstream improvement, including caching and benchmarking, without changing arithmetic or the public hardware interface.

**How:** Introduce a versioned evidence manifest binding contract, proposal, candidate, generated netlists, simulator models, scorer version, fixture identities, tool/image identity, target and requested/completed stages. Give development, audit, RTL, mapping, mapped replay and physical implementation separate states. Publish final bundles atomically after validation and produce a concise offline inspect/report command from the same data. Keep compatibility readers explicit: an old artifact is legacy evidence until reverified, never silently upgraded.

**Done when:** Every accepted consumer verifies the exact artifact chain and required evidence scope. Reports never infer physical timing or overall acceptance from component passes. All historical demos remain replayable through explicit legacy handling or re-verification.

**Main constraint:** Do not turn checksums into a claim of signed provenance or a multi-tenant security boundary. Do not include credentials, private host configuration, or audit examples in provider-visible metadata.

### 2. Make generation and audit recovery durable and deterministic — `fpga-2hx.2`

**Observed:** translate.translate compares source, contract, vector digests and provider/model on resume, but omits image identity, synthesis mode and replay-file identity. It writes history after evaluation and can leave summary.json in_progress on interruption. Wi-Fi backend, Viterbi and receiver loops independently reuse response.json files with fewer identity checks. The shared write_json already uses atomic replacement and should be reused.

**Why this is worth building:** A failed container or interrupted laptop session should cost unfinished work, not an entire experiment. Resume must also preserve what was actually tested: changing from RTL-only to mapping or replaying a different proposal cannot inherit an old success. Persisting the selected candidate before opening audit data makes the research boundary inspectable. The benefit is particularly concrete for the Wi-Fi flow, where builds take many minutes and each stage currently has its own recovery behavior.

**How:** Add an explicit per-run journal with prepared, generation, development, frozen-selection, audit, passed, failed and interrupted states plus per-stage completion. Snapshot immutable inputs and execution policy, enforce exclusive run ownership, recover only complete committed artifacts, and preserve previous results. Share lifecycle helpers across existing loops without forcing their different interfaces into one evaluator. Audit failure remains terminal for candidate selection.

**Done when:** No stale success survives an identity mismatch or interruption. A saved generation response is reused without pretending prior tool execution applies to a new environment. All generator paths follow the same candidate-freeze and terminal-audit rules.

**Main constraint:** Avoid silently spending new model calls during recovery; preserve round accounting. Treat stale locks cautiously and retain journals rather than deleting another active run.

### 3. Reuse verified compilation products while rerunning independent checks — `fpga-2hx.3`

**Observed:** vhdl.evaluate compiles, maps and builds again when translate evaluates the selected candidate under final/. wifi_hardware.evaluate similarly combines synthesis, simulator building and fixture replay. Existing Wi-Fi mapped jobs already reuse one executable within a single evaluation, and cosim already has a persistent executable for SLAM.

**Why this is worth building:** Compilation and mapping are deterministic work for a fixed artifact and toolchain; development and audit need different stimuli and scoring, not necessarily a second synthesis. Separating these stages can save substantial repeated work without weakening independence. The code has useful existing seams, especially the Wi-Fi mapped executable and SLAM server, so this is an incremental improvement. Speedup must be measured from stage timings; the plan makes no numerical performance promise in advance.

**How:** Split compile/map/build artifacts from evaluation. Introduce a bounded content-addressed build cache keyed by HDL, ABI, tool/image/platform identity, mapping flags, driver and primitive-model hashes. Keep fresh per-evaluation workspaces, traces and scoring. Include stimulus in a key whenever it is compiled into the driver. Never cache acceptance as a substitute for executing a newly requested development or audit case set.

**Done when:** Warm runs avoid eligible compilation while preserving fresh RTL four-state and mapped two-state checks as requested. Cache bypass produces equivalent evidence and no audit verdict is inherited from development.

**Main constraint:** A stale or under-specified key can produce false evidence. Start with build-only reuse, keep scorer/case identity in evaluation records, and benchmark disk usage as well as time.

### 4. Unify FPGA resource accounting and explicit target budgets — `fpga-2hx.4`

**Observed:** vhdl.evaluate counts LUT1..6 and the number of RAMB cells. wifi_hardware.mapped_resources additionally counts distributed RAM and SRLs, distinguishes BRAM18 equivalents and rejects unmapped logic. Wi-Fi has compact and xc7a200t profiles; generic TranslationSpec uses max_brams without a shared unit definition.

**Why this is worth building:** The same netlist should not receive different cost estimates depending on the command that produced it. Missing RAM LUTs can make a budget pass misleading, and fourteen 36-Kibit blocks should not be compared blindly with a budget expressed in 18-Kibit equivalents. Reusing the stronger existing Wi-Fi accounting makes resource gates easier to trust and produces a common baseline for compact-receiver work. This is bounded, testable work with immediate relevance to the repository.

**How:** Create one versioned technology-resource normalizer with raw cell counts, logic LUTs, RAM LUTs, SRL LUTs, FFs, DSPs and named BRAM units. Preserve legacy max_brams behavior through an explicit compatibility mode or versioned migration; never silently reinterpret a stored contract. Distinguish device capacities from user budget profiles and from physical placement/timing feasibility.

**Done when:** Resource reports use one implementation, carry explicit units and fail closed on unsupported accounting. Existing contracts get deterministic compatibility behavior and no larger target is selected automatically to force acceptance.

**Main constraint:** Cell utilization is not physical fit or timing closure. New primitive weights need primary-source justification and fixtures before becoming accepted accounting rules.

### 5. Validate and explain hardware contracts before expensive generation — `fpga-2hx.5`

**Observed:** contracts.py already checks widths, output-field overlap, error bounds and vector encodings, but packed input layouts and valid ranges live in free text. translate loads audit vectors only after selection, and paths/source suffix checks happen inside execution. There is no dedicated validation CLI or machine-readable diagnostic inventory.

**Why this is worth building:** An invalid vector, misunderstood bit layout or wrong file path should be found before a model call and several minutes of tool work. Structured input descriptions also reduce ambiguity when translating a new kernel. The existing strict dataclasses provide a good foundation; this idea adds an explanatory front end and optional metadata rather than relaxing validation. Audit isolation is central: normal generation preflight must not inspect or reveal audit values to the provider.

**How:** Add a validate command with stable diagnostic codes, JSON output and a readable packing/units summary. Introduce optional versioned input-field/range metadata with explicitly documented reserved bits and packing behavior. Validate development vectors, path accessibility, feedback scheduling constraints and requested resource units. An explicit offline audit-validation mode may check held-out schemas, but its data and diagnostics must remain outside generation prompts and candidate selection.

**Done when:** Users get actionable diagnostics before expensive execution. Existing prepared contracts remain accepted, exact 256-bit values stay exact, and no input metadata changes the trusted oracle or weakens existing output checks.

**Main constraint:** Audit schema inspection is separate from model-visible preflight; revealing held-out values even in an error message can contaminate selection. Range metadata documents the contract rather than proving a general compiler analysis.

## The next ten

### 6. Schedule tool jobs within an explicit CPU and memory budget — `fpga-2hx.6`

Per-container limits do not prevent several independent containers from collectively overcommitting a laptop VM. A budget-aware scheduler makes the expensive receiver workflow more predictable and gives users enough progress information to distinguish a long compilation from a hang.

**Grounding:** Toolchain.command hardcodes four CPUs per container and takes one global memory limit; Wi-Fi defaults to 6144m and up to four mapped jobs on an 8-GiB VM. Verilator/make use their own -j2 settings. Existing longest-stream-first ordering is useful, but there is no aggregate reservation or per-stage telemetry.

**Approach and acceptance:** Represent stage CPU, memory reservation, timeout and parallelism separately from container hard limits. Schedule against a declared or safely detected VM budget with headroom, retaining deterministic case ordering. Capture stage start/end, queue delay, duration and exit cause; memory usage is measured where supported and explicitly unknown otherwise. No stage launch exceeds the declared aggregate reservation budget. A timeout or interruption releases resources and leaves a diagnostic result; throughput measurements separate queue delay from execution time.

### 7. Make installed demos and task-specific environment checks dependable — `fpga-2hx.7`

The first successful replay is a compelling entry point, but it should work from a normal installation and should not require model credentials. A task-aware doctor can say exactly what SHA replay, Wi-Fi mapping or optional XLS needs instead of reporting one broad ready flag.

**Grounding:** cli.doctor requires codex even for replay and inspects only the default tool image. Wi-Fi uses a different image and larger memory requirements. problem.ROOT points above the package while pyproject.toml only packages fpga_lab and hdl assets, so examples and script lookups rely on an editable checkout.

**Approach and acceptance:** Ship required immutable example assets as package resources and resolve them independently of the working directory. Separate source-checkout development helpers from supported installed commands. Extend doctor with task profiles for replay, generation, Wi-Fi, SLAM and XLS, explaining missing image, architecture, tool capability, optional dependency and VM resource prerequisites. Supported installed demos can locate all required assets; doctor readiness is specific to a task and evidence from bounded probes. Neither inspection nor setup silently stops/resizes the VM or starts model generation.

### 8. Turn failures into typed diagnostics and reproducible development cases — `fpga-2hx.8`

A syntax error, incorrect output and a VM memory failure call for different actions. Stable failure categories and a minimal replay bundle help both humans and the repair loop work on the actual problem. Preserving the failing stream also avoids rerunning a long suite just to recover overwritten evidence.

**Grounding:** translate catches many exceptions as provider_error even when they originate elsewhere; evaluators mostly return truncated error strings. Generic scoring keeps eight numerical examples, and Wi-Fi serial simulation overwrites shared trace/debug/log files while preserving per-case result JSON.

**Approach and acceptance:** Define diagnostic stage/cause/retryability codes with a full log path and concise message. Keep per-case artifacts and a bounded failure window around the first protocol or numeric mismatch. Add development-only minimization that preserves reset, feedback-chain and packet context and confirms the reduced case reproduces on the same toolchain; never send audit failures into repair. Failures identify whether the candidate or environment needs attention, and saved development reproducers work without generation. Audit evidence remains available for inspection but never enables candidate repair within the same selection run.

### 9. Give generated receiver stages explicit, checked interface contracts — `fpga-2hx.9`

Separately verified blocks can still fail when composed with incompatible rates, reset rules or packing. A small shared contract for the existing four stages makes those assumptions reviewable and catches mismatches before a full receiver build. It also reduces dependence on fragile textual substitutions without promising automatic hardware architecture discovery.

**Grounding:** wifi_generate.combined_receiver replaces dut names textually and checks selected VHDL/result hashes. wifi_backend.backend_tb modifies a testbench with string replacement, and concrete block interfaces/schedules live across prompts and source. The architecture documentation explicitly leaves automatic interface generation for later.

**Approach and acceptance:** Create versioned manifests for ports, widths, signed packing, handshakes, maximum service time, reset semantics and dependencies. Generate trusted adapter/testbench declarations from those manifests, check composed instance names and scheduling compatibility, and require evidence for each exact component version. Retain the current fixed top-level interface. Every composed connection and scheduling assumption is recorded and checked, and incompatible blocks fail with an actionable explanation. Component acceptance never implies complete-receiver acceptance.

### 10. Broaden adversarial streaming and rejection/recovery coverage — `fpga-2hx.10`

Passing one stall phase or reset location does not establish behavior at packet boundaries, during output drain or at counter rebasing. Seeded adversarial schedules can uncover boundary failures while preserving the strong tests already present. The missing unsupported-header case is a concrete coverage gap, not a reason to expand the decoder beyond legacy PHY scope.

**Grounding:** The current Wi-Fi suite already tests stalls, reset, malformed SIGNAL parity, sustained short/max frames and counter rebasing. COVERAGE.md marks unsupported HT-header rejection pending. Generic stimulus uses a fixed bubble/reset pattern, and Wi-Fi stalls use a fixed periodic schedule.

**Approach and acceptance:** Add independently specified unsupported-header/recovery fixtures and seeded combinations of valid bubbles, legal stalls, reset positions and long-lived stream boundaries. Define finite stall/service assumptions explicitly, then test packet-byte stability, metadata, overflow, bounded backlog and reacquisition under both RTL and mapped simulation. Keep directed regression cases alongside randomized runs. Coverage records enumerate exact directed cases and seeds, every failure is reproducible, and no existing rate, maximum-frame, backlog or reset check is removed to pass the expanded suite.

### 11. Measure Wi-Fi reception quality against the floating reference — `fpga-2hx.11`

Exact packet bytes on successful cases cannot quantify how often the hardware loses packets under noise. Paired sensitivity curves show whether bounded arithmetic and survivor history cause a practical regression relative to the existing floating decoder. This directly addresses a documented discrepancy and produces useful guidance for future area reductions.

**Grounding:** DISCREPANCIES.md identifies bounded Viterbi survivor history as an unquantified quality tradeoff, and COVERAGE.md lists receiver sensitivity curves as pending. Synthetic fixtures include a few fixed noise/CFO/channel settings but no sweep with packet-error denominators.

**Approach and acceptance:** Freeze the candidate and sweep predefined SNR, CFO, channel, payload-length and legacy-rate cases using independently known transmitted frames. Score missed/extra/corrupt packets against those frames even when the reference also fails. Report paired packet-error rates, counts, uncertainty and measured hardware/reference gaps; keep development sweeps and confirmatory held-out sweeps separate. Reports state rate, payload size, channel, CFO, SNR convention, trial count, uncertainty and exact candidate/tool hashes. The floating decoder never determines which transmitted packets count in the denominator.

### 12. Build a reproducible small-kernel benchmark corpus and trial runner — `fpga-2hx.12`

A repeatable corpus tells maintainers whether a change improves general translation or only one demo. Starting with bounded FIR and dot-product kernels adds useful signal without building a benchmark bureaucracy. Repeated trials and complete failure accounting make model, prompt and architecture experiments interpretable.

**Grounding:** benchmarks.py prepares SHA and normalizer, SLAM/FFT have their own contracts, and scripts/slam_regression.py runs one domain-specific integration suite. docs/architecture.md calls for more kernels and repeated measurements before comparative compiler claims.

**Approach and acceptance:** Create a versioned benchmark registry with source, finite contracts, independent oracles, dev/audit partitions and required evidence scopes. Wrap existing kernels first, then add FIR and dot product with clearly bounded semantics. A trial runner records seeds, provider/model, elapsed stages, repair counts, resources and outcomes; token/cost fields are unknown unless supplied by reliable provider evidence. One manifest can reproduce a campaign, all attempts remain visible and reported improvements are tied to comparable cases and scopes. No SOTA or comparative superiority claim follows from a few successful demos.

### 13. Add bounded CI tiers that exercise the full receiver and installed package — `fpga-2hx.13`

A local success is easier to maintain when regressions in package assets, Wi-Fi compilation and mapped memory behavior become visible automatically. Full receiver runs are expensive, so the right improvement is an explicit tiered test contract with retained evidence and honest skips.

**Grounding:** .github/workflows/ci.yml runs Python tests and the base tools image for SHA/normalizer, but does not build Dockerfile.wifi or invoke complete receiver verification. tests/test_wifi.py has one opt-in RAM-model hardware check and skips pinned captures when absent.

**Approach and acceptance:** Keep fast Python and small-kernel checks on ordinary changes. Add a bounded Wi-Fi RTL/primitive smoke tier plus scheduled or manually dispatched full mapped receiver, sustained-stream and optional integration campaigns. Give each tier resource/time limits, pinned input identities, exact required-case lists and artifacts, and separate missing prerequisites from a successful tested result. A reader can tell exactly which scope each CI result exercised. Routine PR checks remain bounded, while full receiver behavior has a reproducible scheduled/manual gate with diagnostic artifacts.

### 14. Optimize the receiver toward the existing compact resource goal — `fpga-2hx.14`

Reducing area could make the receiver useful on smaller devices, but guessing at RTL simplifications risks losing the scheduling behavior that now sustains input. A measured, stage-attributed optimization campaign focuses effort where resource savings are real and retains throughput and decoding quality as hard constraints.

**Grounding:** The recorded complete receiver maps to 69,048 LUTs and 54,066 FFs, exceeding the unchanged 40,000/40,000 compact profile. It also uses 71 DSPs and fourteen RAMB36E1 blocks. DISC-005 calls this an open optimization target; counters, buffering and scheduling have already needed careful repairs.

**Approach and acceptance:** Capture a reproducible normalized baseline and attribute resource use to stages and generated structures. Investigate high-cost registers/muxes, memory inference, arithmetic sharing and width ranges one change at a time. Keep a Pareto ledger over area, numerical quality and cycle behavior, choose using development evidence only, then freeze and audit the selected variant. Deliver a measured Pareto result with no lost functionality or hidden budget increase. Mark compact achieved only if all original limits pass; otherwise document the best verified reduction and remaining gap without claiming success.

### 15. Import measured Vivado implementation evidence for a named part — `fpga-2hx.15`

The largest remaining credibility gap for a deployable FPGA core is whether it can meet its clock target after implementation. A portable evidence importer and optional runner connect the current verification artifacts to a licensed external tool environment without making Vivado a dependency for local replay.

**Grounding:** vhdl.export_vivado and examples/wifi/vivado.tcl provide handoffs, but README and discrepancies explicitly say physical timing has not been measured on this Mac. The receiver clock is a 200-MHz target and mapped resource fit is not placement/routing evidence.

**Approach and acceptance:** Export an implementation bundle for an explicitly named part and boundary constraint set, plus an optional out-of-context place/route flow. Import utilization, route status, DRC, setup/hold timing and unconstrained-path coverage tied to the bundle hash, Vivado version and settings. Keep synthesized, routed-OOC and board-measured claims distinct. Board pin assignment and bitstream programming are separate work. Reports can establish a narrowly scoped routed implementation result for an exact part and constraint set, including coverage caveats. They never claim board execution, general Fmax or complete timing closure from a target frequency or synthesis-only report.

## Thirty candidates and the selection

Scores are subjective prioritization judgments, not measured performance or user research. Criterion order: robustness, reliability, performance, intuitiveness, user-friendliness, ergonomics, usefulness, compellingness, accretiveness, pragmatism. Each is 1–5; usefulness and pragmatism have weight 2, accretiveness 1.5 and the others 1. Any 1 or average below 3 is a hard cut. Final order also reflects observed gaps, independence, enabling value and practical scope; it is not a mechanical score sort.

| Candidate | Ten scores | Weighted / 5 | Decision |
| --- | --- | ---: | --- |
| 1. Bind every verification claim to an immutable artifact bundle | 5/5/3/5/5/5/5/5/5/5 | 4.84 | Selected; rationale above |
| 2. Make generation and audit recovery durable and deterministic | 5/5/4/5/5/5/5/4/5/5 | 4.84 | Selected; rationale above |
| 3. Reuse verified compilation products while rerunning independent checks | 4/4/5/4/5/5/5/5/5/4 | 4.60 | Selected; rationale above |
| 4. Unify FPGA resource accounting and explicit target budgets | 5/5/3/5/5/4/5/4/5/5 | 4.68 | Selected; rationale above |
| 5. Validate and explain hardware contracts before expensive generation | 5/4/3/5/5/5/5/4/5/5 | 4.68 | Selected; rationale above |
| 6. Schedule tool jobs within an explicit CPU and memory budget | 5/5/4/4/5/4/5/4/5/4 | 4.52 | Selected; rationale above |
| 7. Make installed demos and task-specific environment checks dependable | 4/4/3/5/5/5/4/4/5/5 | 4.44 | Selected; rationale above |
| 8. Turn failures into typed diagnostics and reproducible development cases | 5/4/3/5/5/5/4/4/5/4 | 4.36 | Selected; rationale above |
| 9. Give generated receiver stages explicit, checked interface contracts | 5/5/3/4/4/4/4/4/5/4 | 4.20 | Selected; rationale above |
| 10. Broaden adversarial streaming and rejection/recovery coverage | 5/5/3/4/4/4/4/4/5/4 | 4.20 | Selected; rationale above |
| 11. Measure Wi-Fi reception quality against the floating reference | 4/4/3/4/4/4/4/5/5/4 | 4.12 | Selected; rationale above |
| 12. Build a reproducible small-kernel benchmark corpus and trial runner | 4/4/3/4/4/4/4/5/5/4 | 4.12 | Selected; rationale above |
| 13. Add bounded CI tiers that exercise the full receiver and installed package | 5/5/3/4/4/4/4/4/5/4 | 4.20 | Selected; rationale above |
| 14. Optimize the receiver toward the existing compact resource goal | 4/4/5/4/4/3/4/5/5/3 | 4.04 | Selected; rationale above |
| 15. Import measured Vivado implementation evidence for a named part | 4/4/3/4/4/3/4/5/5/3 | 3.88 | Selected; rationale above |
| 16. Scoped formal proofs for small blocks | 5/5/3/3/3/3/4/4/4/3 | 3.68 | Deferred: Valuable follow-on after explicit ABIs; first resolve frontend, reset assumptions and proof-engine support. Do not make full-receiver proof the laptop acceptance gate. |
| 17. Automatic fixed-point range and error analysis | 4/4/4/4/4/4/4/5/4/2 | 3.76 | Deferred: Broad analysis requires numerical semantics beyond the current finite contracts; begin with explicit input metadata and measured kernels. |
| 18. Automatic architecture/decomposition search | 4/3/4/3/4/3/4/5/4/2 | 3.52 | Deferred: The current interfaces are hand-specified; checked composition and baseline campaigns should precede this research expansion. |
| 19. General IEEE-754 hardware translation | 3/3/2/3/3/3/3/4/3/1 | 2.68 | Hard cut: Large semantic and resource expansion beyond the demonstrated bounded fixed-point route. |
| 20. Board integration and live RF receive demo | 3/3/3/4/4/3/4/5/4/2 | 3.44 | Deferred: Requires a named board, RF front end and constraints not provided in this repository; preserve an optional handoff first. |
| 21. Browser dashboard for experiment runs | 3/3/3/5/5/4/3/4/3/3 | 3.48 | Deferred: An offline inspect/report flow addresses the immediate evidence problem with less infrastructure. |
| 22. Distributed remote verification farm | 4/4/5/3/3/3/3/4/4/2 | 3.36 | Deferred: First fix local stage reuse, provenance and admission control; remote orchestration adds trust and deployment work. |
| 23. Model/provider tournament and automatic model routing | 3/3/4/3/3/3/3/4/3/2 | 3.00 | Deferred: Repeated comparable trials are a prerequisite; routing before evidence risks optimizing anecdotal outcomes. |
| 24. Cloud result registry with shared accounts | 3/3/3/3/3/3/2/4/3/2 | 2.76 | Hard cut: No demonstrated need for a service, tenancy or hosted storage; relocatable local bundles suffice. |
| 25. General variable-latency streaming contract | 4/4/4/3/3/3/3/4/4/2 | 3.28 | Deferred: Changes the core fixed-cycle interface and scorer model; check existing Wi-Fi stage interfaces first. |
| 26. AXI-Stream/AXI-Lite integration wrapper library | 4/4/3/4/4/3/3/4/4/2 | 3.36 | Deferred: Useful with a named integration target; avoid selecting bus semantics without a consumer. |
| 27. New FFT and FEC algorithm portfolio | 3/3/4/3/3/3/3/4/3/2 | 3.00 | Deferred: Sensitivity and hotspot measurements should identify whether alternate algorithms address a real limit. |
| 28. Power estimation and thermal optimization | 3/3/3/3/3/3/3/4/3/2 | 2.92 | Deferred: Requires implementation and credible activity data before claims become useful. |
| 29. Automatic execution of uploaded Python/MATLAB oracles | 2/2/3/4/4/4/3/4/2/1 | 2.72 | Hard cut: Would expand the execution/trust boundary; retain independently supplied trusted vectors. |
| 30. Broad code formatting and module rewrite | 3/3/3/3/3/3/2/2/2/4 | 2.80 | Hard cut: Readable code is helpful, but concrete evidence, runtime and integration gaps provide stronger user value. |

## Execution and verification

| Rank | Feature | Core | Integration | Unit tests | Real E2E |
| ---: | --- | --- | --- | --- | --- |
| 1 | `fpga-2hx.1` evidence | `fpga-2hx.1.1` | `fpga-2hx.1.2` | `fpga-2hx.1.3` | `fpga-2hx.1.4` |
| 2 | `fpga-2hx.2` resume | `fpga-2hx.2.1` | `fpga-2hx.2.2` | `fpga-2hx.2.3` | `fpga-2hx.2.4` |
| 3 | `fpga-2hx.3` cache | `fpga-2hx.3.1` | `fpga-2hx.3.2` | `fpga-2hx.3.3` | `fpga-2hx.3.4` |
| 4 | `fpga-2hx.4` resources | `fpga-2hx.4.1` | `fpga-2hx.4.2` | `fpga-2hx.4.3` | `fpga-2hx.4.4` |
| 5 | `fpga-2hx.5` preflight | `fpga-2hx.5.1` | `fpga-2hx.5.2` | `fpga-2hx.5.3` | `fpga-2hx.5.4` |
| 6 | `fpga-2hx.6` scheduler | `fpga-2hx.6.1` | `fpga-2hx.6.2` | `fpga-2hx.6.3` | `fpga-2hx.6.4` |
| 7 | `fpga-2hx.7` doctor | `fpga-2hx.7.1` | `fpga-2hx.7.2` | `fpga-2hx.7.3` | `fpga-2hx.7.4` |
| 8 | `fpga-2hx.8` diagnostics | `fpga-2hx.8.1` | `fpga-2hx.8.2` | `fpga-2hx.8.3` | `fpga-2hx.8.4` |
| 9 | `fpga-2hx.9` interfaces | `fpga-2hx.9.1` | `fpga-2hx.9.2` | `fpga-2hx.9.3` | `fpga-2hx.9.4` |
| 10 | `fpga-2hx.10` streaming | `fpga-2hx.10.1` | `fpga-2hx.10.2` | `fpga-2hx.10.3` | `fpga-2hx.10.4` |
| 11 | `fpga-2hx.11` sensitivity | `fpga-2hx.11.1` | `fpga-2hx.11.2` | `fpga-2hx.11.3` | `fpga-2hx.11.4` |
| 12 | `fpga-2hx.12` benchmarks | `fpga-2hx.12.1` | `fpga-2hx.12.2` | `fpga-2hx.12.3` | `fpga-2hx.12.4` |
| 13 | `fpga-2hx.13` ci | `fpga-2hx.13.1` | `fpga-2hx.13.2` | `fpga-2hx.13.3` | `fpga-2hx.13.4` |
| 14 | `fpga-2hx.14` compact | `fpga-2hx.14.1` | `fpga-2hx.14.2` | `fpga-2hx.14.3` | `fpga-2hx.14.4` |
| 15 | `fpga-2hx.15` vivado | `fpga-2hx.15.1` | `fpga-2hx.15.2` | `fpga-2hx.15.3` | `fpga-2hx.15.4` |

Begin with the evidence schema and resource normalizer. Contract validation, task-specific installation checks, interface specifications, protocol fixtures and failure taxonomy can be designed independently. Wire resume and build reuse to the evidence identity before relying on cached work. Do not wait for all 15 features to ship: each feature carries its own acceptance and regression evidence.

Unit tests target boundaries and failure behavior. Every feature also has a real-E2E task with case IDs/seeds, tool and artifact identity, expected/actual results, stage timing, full failure logs and machine-readable summaries. Replay is the default for validation; live model campaigns are explicit and budgeted. Required missing prerequisites produce incomplete evidence, never a successful test result.

Vivado execution needs an available licensed runner. The parser/handoff work can proceed locally, but its real-tool task remains open until executed. Compact-receiver work can deliver a verified reduction without falsely claiming the unchanged compact budgets are met.

Routed timing is a distinct evidence stage: AMD describes post-route analysis as using routed net delays and calls for route/timing review. See [AMD UG894, sample implementation flow](https://docs.amd.com/r/2024.2-English/ug894-vivado-tcl-scripting/Details-of-the-Sample-Script?contentId=L_5proZ4luz_ANYUi8A4Cg). Formal verification was considered separately; [YosysHQ SBY documentation](https://yosyshq.readthedocs.io/projects/sby/en/stable/) describes its formal flows. This roadmap makes neither formal proof nor board execution a claim of existing simulation.

## Refinement record

Five passes reviewed the stored issues, with a dependency-cycle check after each pass:

1. **Structure:** made all 60 task titles concrete, gave tasks their own acceptance criteria, and specified the four required child deliverables for each feature.
2. **Dependencies:** allowed unit verification and integration to proceed independently after the core. E2E waits for both. Narrowed build-cache integration to the durable run-identity dependency instead of waiting for every resume consumer.
3. **Tests:** added feature-specific negative cases for immutable input snapshots, compiled testbench cache keys, legacy BRAM units, shared directed FFT vectors, cancellation cleanup, known-transmitted-frame denominators and routed timing coverage. Required missing hardware checks remain incomplete.
4. **Self-documentation:** added sibling task references, closeout instructions and detailed rationale comments. Each bead includes enough background, scope, acceptance, risks and validation detail to execute without this report. The Vivado beads include the primary timing reference.
5. **Priorities and scope:** retained all 15 selected capabilities and all 30 dedicated test tasks. The roadmap epic also contains the full selection rationale and deferred alternatives. Local foundation work can proceed while the licensed Vivado execution task awaits its external toolchain.

The final working-tree refresh incorporated concurrently updated Wi-Fi results and coverage: complete receiver RTL and mapped verification are already recorded as accepted. The plan does not propose completing that existing verification as a new feature. The evidence recommendation concerns binding artifacts and keeping reporting surfaces consistent.

The exported backlog contains **76 open issues**: one roadmap epic, 15 features and 60 tasks. It has **80 execution dependencies** and 75 organizational parent-child links. Both `br dep cycles --json` and `bv --robot-insights` report no cycles. Twelve implementation tasks are independently ready.

Use the task filter when choosing executable work; `bv --robot-plan` also displays organizational hierarchy, which is not an instruction to close a parent before starting its children:

```sh
br show fpga-2hx --json
br ready --type task --limit 0 --json
br show fpga-2hx.1.1 --json
```

The recommended first implementation task is `fpga-2hx.1.1`, the evidence manifest and artifact binding foundation. `fpga-2hx.4.1`, the shared resource normalizer, can proceed independently.
