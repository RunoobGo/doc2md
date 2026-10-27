"""Doc2md —— 文本型 PDF / DOCX → Markdown 离线转换管线。

命名纪律（方案 1.4）：
- 对外叙述一律 Doc2md；
- 代码内字面标识保持小写 doc2md（CLI、launchd label、目录名、日志 code）。

版本治理（方案 第一章）：
- PIPELINE_VERSION：管线语义化版本（契约级修订走 minor）。
- SCHEMA_VERSION：IR schema 版本，随迁移脚本演进。
- PLAN_VERSION：开发方案文档版本（主题篇规格变化时递增）。
"""

PIPELINE_VERSION = "2.21.0"   # CH-506：`w:num/w:lvlOverride`（含 `startOverride`）按 ECMA-376 §17.9.15
                              # 覆盖该 numId 的抽象级定义——覆盖层置 `numFmt="none"` / 空 `lvlText`
                              # 时不再凭空造出标记，`startOverride` 也不再被忽略。带该构造的 DOCX
                              # 交付字节随之改变 → minor。CH-504 的三项产物修复（固定 `ZipInfo` 打包 /
                              # 等宽行链接标注 / `numFmt="none"` 不造号）见 2.20.0 历史注释。
SCHEMA_VERSION = 14          # IR 约定 14（文件名与内容的边界）落地
PLAN_VERSION = "3.39"         # 开发方案文档版本（docs/dev-plan.md，文件名不带版本号）

__all__ = ["PIPELINE_VERSION", "SCHEMA_VERSION", "PLAN_VERSION"]
