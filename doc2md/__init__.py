"""Doc2md —— 文本型 PDF / DOCX → Markdown 离线转换管线。

命名纪律（方案 1.4）：
- 对外叙述一律 Doc2md；
- 代码内字面标识保持小写 doc2md（CLI、launchd label、目录名、日志 code）。

版本治理（方案 第一章）：
- PIPELINE_VERSION：管线语义化版本（契约级修订走 minor）。
- SCHEMA_VERSION：IR schema 版本，随迁移脚本演进。
- PLAN_VERSION：开发方案文档版本（主题篇规格变化时递增）。
"""

PIPELINE_VERSION = "2.22.0"   # CH-508：三种间接编号形态接上——样式 `basedOn` 链继承、`w:numStyleLink`
                              # 委托编号样式、`w:lvl/w:pStyle` 反向绑定；`Heading` 无数字不再兜底成
                              # 1 级。带这些构造的 DOCX 交付字节改变 → minor。CH-506 的
                              # `lvlOverride` 覆盖语义见 2.21.0 历史注释。
SCHEMA_VERSION = 14          # IR 约定 14（文件名与内容的边界）落地
PLAN_VERSION = "3.43"         # 开发方案文档版本（docs/dev-plan.md，文件名不带版本号）；CH-509 为
                              # 测试宿主耦合修复轮，CH-510～CH-511 为 B-53 负断言补正例的两批
                              # （均只动测试与文档面，PIPELINE_VERSION 不动）

__all__ = ["PIPELINE_VERSION", "SCHEMA_VERSION", "PLAN_VERSION"]
