# RML知识图谱增量更新实验

基于CRTM（马德里交通管理局）真实GTFS数据的知识图谱增量更新实验：对比全量重构、单线程增量更新、分布式增量更新（Redis任务队列 + 实体版本号乐观并发）三种方式，并包含机制消融（乐观版本号 vs 全局锁）、join成本模型标定与规模外推。

## 结果速览

| 实验 | 数据集 | 关键数字 |
|---|---|---|
| 全量重构（朴素O(n²) join） | Metro | 1842±102 s（join占99.4%） |
| 全量重构（T'式线性化） | Metro | 11.07±0.29 s（相对朴素全量约166×，见报告§6） |
| 单线程增量（10%档，历史主实验） | Metro | 2.35±0.25 s；由报告列出的5个seed重算 |
| 分布式增量 k=4（同JVM复用） | Metro | 稳态1.40±0.05 s；匹配seed2–5的配对加速比2.128±0.101× |
| Urbanos规模扩展（2026-10-06统一会话） | Urbanos 38万行 | seed2–5配对加速比1.329/1.412/1.477×；15对输出一致，详见报告§7.8 |
| 分区倾斜细粒度拆分（2026-10-07） | Urbanos 10%固定ΔD | p=0.75/1.0端到端配对加速比1.112/1.162×，worker映射负载差显著下降，详见报告§7.9 |
| 25%冲突下的倾斜拆分（2026-10-07） | Urbanos 10%固定ΔD + 冲突副本 | 实测s=2.09档配对加速比1.102±0.091×；高倾斜下OCC重试增多，详见报告§7.10 |
| 机制消融 | Urbanos u50档×冲突率{0,5,20}% | A0/A2耗时接近，OCC重试与锁竞争行为不同；不报告未配对的并行加速比 |

完整数据与分析见 **[docs/实验报告.md](docs/实验报告.md)**。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/实验指导.md](docs/实验指导.md) | **实验指导**：从数据获取到各实验的完整可复现步骤（含分布式/Docker一节与全部已知局限） |
| [docs/实验报告.md](docs/实验报告.md) | **实验报告**：结果、分析，以及全过程中遇到的问题与定位过程（重点章节§4、§8） |

## 目录结构

```
├── docs/
│   ├── 实验指导.md           # 实验指导主文档（数据获取→各实验完整步骤）
│   └── 实验报告.md           # 实验报告全文
├── change_generator.py      # ΔD变更生成（Insert/Update/Delete/Composite四操作）
├── compute_delta.py         # before/after按主键diff
├── normalize_mapping.py     # mapping数据源路径改写（含.csv镜像兜底）
├── find_iri_template.py     # 从mapping提取实体IRI模板
├── compute_stale_iris.py    # stale行→待删除subject IRI（含百分号编码对齐）
├── apply_delta_to_graph.py  # baseline −stale +delta
├── run_full_reconstruction.sh / summarize_results.py
├── run_incremental_update.sh / summarize_incremental.py
├── build_delta_mapping.py   # 增量专用窄化映射（join→等价模板，逐quad验证）
├── build_tprime_mapping.py  # T'分解实验映射
├── build_full_linear_mapping.py  # 基线线性化（大feed基线构建）
├── run_distributed_update.py     # 分布式调度器（分区/分发/栅栏/合并/应用）
├── mapping.ttl              # 基础mapping（GTFS-Metro-Bench风格，13个TriplesMap）
├── delta_mapping_base.ttl / tprime_mapping_base.ttl
├── dist/                    # 分布式worker镜像（Java常驻进程+Jedis）
│   ├── src/Worker.java 等
│   ├── Dockerfile.worker    # 注意：构建前需放置rmlmapper.jar（见下方获取）
│   └── lib/fetch_jars.sh    # 下载Jedis等依赖
├── docker-compose.yml       # redis + worker×k（cpus/mem限制，共享卷）
├── ablation/                # 消融实验设计与矩阵驱动
└── results/                 # 时间日志/校验记录（.nq图谱不入库）
```

## 复现要点

1. **依赖**：JDK 21+（RMLMapper 8.1.0要求class版本65）、Python 3.10+、Docker Compose v2。
2. **RMLMapper**：从 [RMLio releases](https://github.com/RMLio/rmlmapper-java/releases) 获取jar放至根目录与`dist/`（或调整Dockerfile挂载）。
3. **数据**：Metro feed来自datos.crtm.es（网络受阻时走ECR/ArcGIS侧通道，见指导文档§1）；Urbanos feed获取方式同。
4. **顺序**：按 `docs/实验指导.md` §0–§5 执行metro主线；§6 为分布式；消融见 `ablation/README.md`。
5. **注意**：所有脚本默认在仓库根目录执行；跨数据集实验需传入对应baseline图谱（`run_incremental_update.sh`第9参数）。

## 引用的外部组件

- [RMLMapper](https://github.com/RMLio/rmlmapper-java)（Apache-2.0）
- [GTFS-Madrid-Bench](https://github.com/oeg-upm/gtfs-bench) 的映射模板（mapping.ttl源自其CSV映射改造）
- [Jedis](https://github.com/redis/jedis)（MIT）
- CRTM开放数据（GTFS Red de Metro / Autobuses Urbanos）
