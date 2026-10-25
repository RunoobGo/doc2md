"""Doc2md —— 文本型 PDF / DOCX → Markdown 离线转换管线。

命名纪律（方案 1.4）：
- 对外叙述一律 Doc2md；
- 代码内字面标识保持小写 doc2md（CLI、launchd label、目录名、日志 code）。

版本治理（方案 第一章）：
- PIPELINE_VERSION：管线语义化版本（契约级修订走 minor）。
- SCHEMA_VERSION：IR schema 版本，随迁移脚本演进。
- PLAN_VERSION：开发方案文档版本（主题篇规格变化时递增）。
"""

PIPELINE_VERSION = "2.18.0"   # CH-500：交付 meta 摘掉片级内部锚 `shard`、外链链接文字
                              # 补转义、DOCX 行字号改加权众数——三项均改变产物字节 → minor
SCHEMA_VERSION = 14          # IR 约定 14（文件名与内容的边界）落地
PLAN_VERSION = "3.36"         # 开发方案文档版本（docs/dev-plan.md，文件名不带版本号）

__all__ = ["PIPELINE_VERSION", "SCHEMA_VERSION", "PLAN_VERSION"]
