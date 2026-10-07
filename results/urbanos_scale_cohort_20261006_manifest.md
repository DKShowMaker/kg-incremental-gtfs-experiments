# Urbanos Scale Rerun Manifest

## Cohort

- Cohort tag: `urbanos_scale_cohort_20261006`
- Timed run window: 2026-10-06 22:32:36 to 22:39:31 (+08:00)
- Repository: branch `main`, HEAD `3c321c39625c5e3ae3ef493a60391716722f04ca`; source tree was clean before the run.
- Profiles: Urbanos 10%, 25%, 50%; seeds 1-5; `k=4`; 30 end-to-end runs (15 single-thread and 15 distributed).
- Inputs: reused the archived `data/u10_after_seedN`, `data/u25_after_seedN`, and `data/u50_after_seedN` snapshots against `data/urbanos_before`; no input generation occurred during timing. All top-level GTFS `.txt` files other than `stop_times.txt` were byte-identical to the before snapshot.
- Mapped delta rows were verified in both pipelines: 28,677 / 71,693 / 143,386 for 10% / 25% / 50%.
- For order balance, profile order rotated by seed and method order alternated by seed. Workers stayed alive for the whole cohort. The first distributed map (u10 seed 1) was the only JVM cold start; later seed-1 runs were warm. Seed 1 is retained in raw data and excluded uniformly from steady-state summaries, following the existing convention.
- Correctness: all 15 single-thread/distributed output graph pairs were sorted with `LC_ALL=C sort` and compared; every pair was identical.

## Steady-State Results

Mean ± sample standard deviation over seeds 2-5. Speedup is computed per seed as `single / distributed`, then summarized across the four paired ratios.

| Profile | Mapped delta rows | Single-thread (s) | Distributed (s) | Paired speedup |
|---|---:|---:|---:|---:|
| 10% | 28,677 | 12.026 ± 1.193 | 9.050 ± 0.519 | 1.329 ± 0.111× |
| 25% | 71,693 | 15.562 ± 0.676 | 11.032 ± 0.665 | 1.412 ± 0.056× |
| 50% | 143,386 | 21.829 ± 0.757 | 14.816 ± 1.108 | 1.477 ± 0.076× |

## Runtime Environment

- OS: Ubuntu 20.04.6 LTS under WSL2; kernel `6.18.40.1-microsoft-standard-WSL2`; x86_64.
- CPU: Intel Core i5-13400F; WSL exposed 16 logical CPUs (`nproc=16`).
- Memory at preflight: 23 GiB total, 21 GiB available; 6 GiB swap.
- Python: 3.13.12 (`/home/ztr/miniconda3/bin/python3`).
- Host Java: OpenJDK 21.0.7.
- Docker Engine: 28.1.1; Docker Compose: v2.35.1.
- Worker image rebuilt from this checkout: `kg-worker`, image ID `sha256:02d9503191e9a49b35d03647d2f004a72c1699168a20919995108e163f270d62`; base `eclipse-temurin:21-jdk-jammy`.
- Redis: `redis:7-alpine`; Compose resolved `CONSISTENCY_MODE=occ`.
- Worker limits: 4 containers, 1 CPU and 2 GiB memory each; Java heap `-Xmx1750m`.

## SHA-256

The per-seed after-snapshot `stop_times.txt` hashes are recorded in [`urbanos_scale_cohort_20261006_raw.csv`](urbanos_scale_cohort_20261006_raw.csv). Shared inputs and runtime source files:

| File | SHA-256 |
|---|---|
| `data/urbanos_before/stop_times.txt` | `63b9574a84cb581ec97be4267d38c2fb9ceb82b7b4b47b56c86e3c8d1ed36ce9` |
| `results/baseline_graph_urbanos.nq` | `b00e5ffa130f110e786d2f7d32241953c096315e63e38023e105b87212b40d15` |
| `delta_mapping_base.ttl` | `608a4aeb87eab936a40defbbee2241fcce049110a31c98632951efab23392869` |
| `rmlmapper.jar` (same hash as `dist/rmlmapper.jar`) | `819371d49ca47d8ffddae0f34e95f38e8eaaf588ee023e3c2c7527a14d302f58` |
| `docker-compose.yml` | `995298455be0c6ce3e9e657711bf73a2b34136b61b63b442ddcc36622a14210a` |
| `dist/Dockerfile.worker` | `8a42dd6a32041583e9a40f1ba442f2563fa1da593230686b44aa58747a041d0d` |
| `dist/src/Worker.java` | `1d3aa4fb33c87607fa8bdeebc59562e93160bcdb83fb030e1110431ba4c72daf` |
| `run_incremental_update.sh` | `9e13781354a50f52bb39ff329e414f6b352dbb661c1d46fad819607ee06f8ed5` |
| `run_distributed_update.py` | `e6cf78e03262dd20425e6a38b57d3528c048c06b341dde584332c9aafe36ba4c` |
| `compute_delta.py` | `6c4ac6522b4331be7613a891d7a217e6b4d5f9f066fd950a64421a8cbfb0decf` |

## Artifacts

- Per-run timing: `time_incremental_urbanos_scale_cohort_20261006_<profile>_s<seed>.log` and `time_distributed_urbanos_scale_cohort_20261006_<profile>_s<seed>.log`.
- Raw paired values and per-input hashes: [`urbanos_scale_cohort_20261006_raw.csv`](urbanos_scale_cohort_20261006_raw.csv).
- Aggregates: [`urbanos_scale_cohort_20261006_summary.csv`](urbanos_scale_cohort_20261006_summary.csv).
- Full command output: `urbanos_scale_cohort_20261006_batch.log`.
- Output graphs: `kg_incremental_urbanos_scale_cohort_20261006_<profile>_s<seed>.nq` and corresponding `kg_distributed_...nq` files.
