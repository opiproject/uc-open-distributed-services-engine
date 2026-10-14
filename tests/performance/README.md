# performance

Throughput, latency, and packets-per-second (PPS) benchmarks for offloaded
vs. non-offloaded datapaths.

Methodology (required before publishing numbers):
[`docs/test-plan/performance-methodology.md`](../../docs/test-plan/performance-methodology.md).

Expected contents:

- Benchmark scripts (e.g. `trex`/`iperf3`/`testpmd`-based) for VM-to-VM,
  pod-to-pod, and north-south traffic
- Baseline vs. offloaded comparison methodology and result templates
- Regression tracking hooks for `ci/hardware-regression`
