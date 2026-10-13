TOML_BASENAME = "doc2md.toml"

DEFAULT_TOML = '''\
# Doc2md 配置（锚点 v2.3 §4.3 + v2.4 附录 C-1 全文）
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

[s1_5]
sample_pages = "auto"                  # CH-424：判定覆盖全页密度，even5 作为报告采样面
                                       # 可选 "even5"（CH-415 原口径）/ "legacy3"（对照）
min_chars_per_page = 120               # 任一采样页低于此值即判坏页
reject_on_any_bad_page = true          # 任一采样页不达标即拒收（E4221）
page_detail_in_report = true           # 报告附页级明细（哪一页、哪个指标）

[s2]
mode = "auto"                          # §7.2 开关：auto / single / two_phase
max_pages_per_shard = 300              # FR-126 ①：S2 后粗切片大小
shard_overlap_pages = 2                # 片间重叠上下文
dedupe_overlap = true                  # 重叠区按文本指纹去重（CH-423，见方案 v1.1 附录 H）

[chunks]
tokenizer = "gguf-embedded"            # P-49 / CH-402：来源固化
manifest = "vendor/models/llm/tokenizer.manifest.json"

[s4]
preserve_marks_strike = false          # P-65 / CH-417：删除线默认关
link_internal = "plain"                # P-64 / CH-416：goto 内部链接 → 普通文本

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
base_revision_required = true          # ADR-28 / §7.2：false = 灰度 optional 阶段

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
sanitize_fail_action = "reject"        # 不设降级（§7.2）
'''
