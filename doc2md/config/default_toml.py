TOML_BASENAME = "doc2md.toml"

DEFAULT_TOML = '''\
# Doc2md 配置（逐键语义见方案 附录 A；上游锚点 v2.3 §4.3 + v2.4 附录 C-1）
# 命名纪律：字面标识保持小写 doc2md。

[paths]
root = "work"                          # 项目工作根目录
out = "work/out"
reports = "work/reports"
logs = "work/logs"
tmp = "work/tmp"
vendor_models = "vendor/models/llm"

[run]
profile = "standard"                   # v2.1 冻结：三档 lite/standard/strict
offline_assert = true                  # 出站网络断言（NFR 离线纪律）

[s1]
chunk_pages = 32                       # CH-447：每重新打开一次文档最多解析多少页（0 = 整本一次读完）
                                       # 32 页 ≈ 180MB 峰值驻留（实测 320 页样本 1025MB → 177MB），
                                       # 8GB 内存的 Win11 与 16GB Mac mini 同档安全

[s1_5]
sample_pages = "auto"                  # CH-424：判定覆盖全页密度，even5 作为报告采样面
                                       # 可选 "even5"（CH-415 原口径）/ "legacy3"（对照）
min_chars_per_page = 120               # 任一采样页低于此值即判坏页
reject_on_any_bad_page = true          # 任一采样页不达标即拒收（E4221）
page_detail_in_report = true           # 报告附页级明细（哪一页、哪个指标）

[s2]
mode = "auto"                          # 方案 4.3 开关：auto / single / two_phase
max_pages_per_shard = 300              # FR-126 ①：S2 后粗切片大小
shard_overlap_pages = 2                # 片间重叠上下文
dedupe_overlap = true                  # 重叠区按文本指纹去重（CH-423，见方案 第十一章）

[chunks]
tokenizer = "gguf-embedded"            # P-49 / CH-402：来源固化
manifest = "vendor/models/llm/tokenizer.manifest.json"

[s4]
preserve_marks_strike = false          # P-65 / CH-417：删除线默认关
link_internal = "plain"                # P-64 / CH-416：goto 内部链接 → 普通文本

[perf]
workers = 0                            # CH-448：批量转换进程数上限；0 = 自动 min(4, cpu_count())
                                       # 内存纪律：并发数 × [s1].chunk_pages 决定驻留峰值，
                                       # 低配机（8GB Win11）保持 workers ≤ 4 + chunk ≤ 32
queue_async = false                    # CH-449：POST /jobs 只入队、由 `doc2md work` 消费
                                       # 默认 false = 保持同步返回既有契约（方案 8.1 红线不变）
min_free_mb = 512                      # NFR-6：写产物 / 备份前的剩余磁盘门禁（不足 → 413/E4131）
disk_safety_factor = 3                 # 需求 = 待写字节 × 该系数（zip + 临时 + 解压余量）
stale_job_minutes = 30                 # CH-448：work 进程被 OOM 杀掉后，running 超过此分钟数
                                       # 视为遗骸 → 重新入队（低配机上这是常态而非常态外）

[watch]
stable_seconds = 5                     # P-58 / CH-411
follow_symlinks = false                # P-59
max_depth = 20
skip_hidden = true
max_files_per_dir = 5000

[api]
bind = "127.0.0.1"
port = 8090
token_roles = ["admin", "editor", "reader"]   # P-54 / CH-407
revoke_effective_seconds = 5           # NFR-9：吊销即时生效窗口
base_revision_required = true          # ADR-28 / 方案 8.5：false = 灰度 optional 阶段

[llm]
bind = "127.0.0.1"
port = 8080
auth = false                           # P-55 / CH-408：已知接受项登记
op_whitelist = ["split_block", "merge_block", "fix_heading_level",
                "fix_list_marker", "mark_table_boundary"]

[logs]
rotate_mb = 50                         # NFR-19 / CH-413
rotate_keep = 5

[ops]
events_retention_days = 30
backup_local_dir = "work/reports/backups"
backup_external_encrypted = true       # NFR-18
backup_rotation_days = 14              # 已删数据 ≤14 天从全部副本轮出
work_dir_mode = "700"

[security]
max_name_bytes = 128                   # FR-127
sanitize_fail_action = "reject"        # 不设降级（方案 6.1）【声明性：代码未读取】
'''
