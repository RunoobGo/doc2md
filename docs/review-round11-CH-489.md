# Doc2md 第十一轮全面审查与优化报告（CH-489）

> 审查日期：2026-10-18
> 审查基线：`aa69676`（`main`）之上的工作树；本机 Windows 11 / Python 3.12.10 / 仓库自带 `env`
> 审查性质：设计、代码、文档、测试、CI 五面只读审查 + A 类修复直接实施
> 原则：功能全面、性能优先；需要本机以外环境或人工验证的事项只登记，每批交付重复提醒

## 1. 执行摘要

本轮按「功能全面、性能优先」复核了 41 个 Python 模块、54 个测试文件、配置样例、CI 与
`docs/dev-plan.md` / README / `ops/` 运维文档。合并去重后形成 **15 个 A 类处置项**（本机可改可验证，
全部落地）与 **6 个 B/C 类登记项**（需目标机、外部介质、真实业务语料或人工判断）。

最重要的结论：

1. **失败面仍有“空产物成功”**：0 页 PDF 能通过 S1.5 门禁并产出空 Markdown；本轮改为
   `E4221 / no_pages`。
2. **显式结构被保守规则丢掉**：DOCX 的单行表、单列表仍被降级为普通段落；本轮保留 GFM 表结构，
   PDF 单行候选仍按「宁漏勿误」退回段落。
3. **分页硬顶存在旁路**：`?limit=0` 在 `/docs`、`/audit`、`/audit/deletions` 被当成“无界”，
   审计接口每行还带 before/after 全文；本轮统一归一为默认 500 并保留 total。
4. **数据安全有两处窗口**：删前快照直接写最终名且不做完整性校验；`paths.tmp` 可配成工作根，
   `sweep_tmp` 会在下一次启动/日报时清掉 state/out/logs。本轮分别改为原子快照 + quick_check、
   配置期拒绝危险 tmp。
5. **队列/服务语义有两处不一致**：watch 会把附着到的既有 queued/running 作业提前标 done；
   内容漂移后任务仍指向入队时的 doc_id，`/jobs` 显示 done 而 `/docs/<id>` 404。本轮修正状态机
   并让漂移任务指向实际产物，同时把同批重复路径的 IR 读取/落库收敛为一次。
6. **文档口径继续漂移**：README 的测试计数停留在 CH-486，`ops/llm.md` 的模型路径少写了
   `paths.root`，示例配置默认指向不存在的 `/Volumes/...`，真实语料 README 仍称 `.doc` 不支持。
   本轮一并收口，并把 CH-489 登记进 dev-plan。

## 2. A 类处置项（已实施）

| 编号 | 级别 | 发现 | 处置 |
| --- | --- | --- | --- |
| R11-01 | HIGH | 0 页 PDF 被当作成功，产出空 Markdown | `GateCollector.finish()` 对 `total<=0` 记 `no_pages` 并抛 E4221；新增零页 PDF 回归 |
| R11-02 | MED | DOCX 显式单行表/单列表格被降级为段落 | `analyze` 对 `row_explicit` 允许 1 行、1 列成表；PDF 单行仍要求 ≥2 单元格 |
| R11-03 | MED | `limit=0` 绕过 500 行硬顶，审计正文可无界物化 | `_window()` 将 0 归一为端点默认值后再硬顶；`/docs`、`/audit`、`/audit/deletions` 同步 |
| R11-04 | HIGH | `paths.tmp` 可等于工作根或祖先，`sweep_tmp` 会删工作根内容 | `config.validate()` 按解析后真实路径拒绝 tmp 等于/包含 root |
| R11-05 | HIGH | 删前快照直接写最终名、无 quick_check，崩溃可留半截“可恢复”库 | `.part` + quick_check + fsync + 原子 rename；写前先收紧目录 ACL，失败清 part/sidecar |
| R11-06 | HIGH | watch 对附着到的既有作业提前 `mark(done)`，把在途作业伪装成完成 | 仅当 `not (attached or idem_hit)` 时推进 done；新增参数化回归 |
| R11-07 | HIGH | 内容漂移后任务仍指向 enqueued doc_id，客户端 GET 404 | 漂移时把 `tasks.doc_id` 更新为 consumed doc_id，原 id 留在 `content_drift`；有活动冲突时保留原值并留 `task_doc_id_updated=false` |
| R11-08 | MED | 同路径多作业重复读 IR、重复预处理并重复写同一正文 | `drain_once` 按 zip 缓存 IR、按 consumed doc_id 只落库一次；任务/事件仍逐条推进 |
| R11-09 | MED | 外部链接 label/URL 可截断 Markdown，导出路径可被路径段型 doc_id 逃逸 | label 转义 `[]\\`/换行，URL 百分号编码空格与括号；`_artifact_zip()` 校验单路径段与 containment |
| R11-10 | MED | zip 被误删后 GET markdown 永远 404，尽管 DB 是事实源 | 缺失产物时按 gate={} 从 blocks 重建；失败明确 E5031，不返回空正文 |
| R11-11 | MED | `_rebuild_artifact` 对无 gate_json 的旧库直接返回 None | 移除早退；报告降级为空 gate，块正文仍可重建 |
| R11-12 | LOW | LLM server 候选 `allowed_ops` 与配置白名单不一致，空白名单仍提示默认 5 项 | allowed_ops/prompt 白名单与配置求交；空列表就是空列表 |
| R11-13 | MED | `paths.out` 可经 PUT/reload 运行期修改，旧产物在删除/导出两侧失联 | 加入运行期不可变键；reload 时钉回当前值，改输出根须重启 |
| R11-14 | LOW | 配置样例 `backup_external_dir` 指向不存在的 `/Volumes/...`，与“留空跳过”注释矛盾 | 样例恢复空串，并注明未配置时周报 skipped |
| R11-15 | LOW | 文档计数/路径/能力口径漂移 | README 计数与版本、dev-plan 版本/CH-489/附录 F/G 活锚点、ops/llm.md 模型路径、真实语料 README 的 `.doc` 口径全部更新 |

## 3. 实施要点

- **门禁**：零页是“没有可转换内容”，不是“文本层稀疏”；`auto`/`even5`/`legacy3` 三档都在判定前给出
  `no_pages`，报告 detail 保留 reasons。
- **表格**：DOCX `w:tbl` 是显式对象，单行/单列仍渲染为 GFM；PDF 没有显式对象，单行/单列仍按保守
  口径退回段落，避免把版面空距误判成表。
- **分页**：显式 0 不再等于“全部”；端点仍保留 `total/offset/limit`，默认 500 与硬顶 500 一致。
- **路径安全**：`paths.tmp` 的判定在 `config.validate()` 用解析后的真实路径做祖先/相等判断；
  `paths.out` 与 `paths.root` 同属进程级绑定，运行期不可改。
- **删前快照**：`predelete_snapshot` 与 cron `snapshot` 同口径，先 `.part` 再校验/落盘/改名；
  目录权限在写入前收紧，避免私有数据在宽松 ACL 下短暂可见。
- **watch/worker**：watch 只对自建 job 推进终态；worker 的 drift 任务改指 consumed doc_id，
  同批同 zip 只读一次 IR，同一 consumed doc 只落库一次（仍逐任务留 detail/事件）。
- **渲染**：链接 label 的 `[]\\` 与换行转义；URL 中空格、括号、尖括号百分号编码，普通 URL 字节不变。
- **LLM**：server 模式发给模型的 `allowed_ops` 不再包含配置外的算子；空白名单与
  `OpValidator` 的“全部拒绝”语义保持一致。

## 4. 验证

| 项目 | 结果 |
| --- | --- |
| `pytest -m gate`（Windows 本机） | **395 项 = 394 passed / 0 failed / 1 platform skip（48.60s）** |
| `pytest`（Windows 本机全量） | **991 项 = 983 passed / 0 failed / 8 platform skips（124.03s）** |
| `ruff check .` | All checks passed |
| `doc2md doctor` | exit 0；`pipeline 2.11.0` |
| 合成综合语料 | **21/21 通过**（20 目标 Markdown + 1 扫描件 E4221 拒收） |
| 真实语料 offline/server | **14/14 + 14/14 成功**；11/14 `content.md` 逐字节一致；3 份仅 `merge_block`/标题层级差异，字符相似度 0.999264 / 1.000000 / 0.999584，无整段正文丢失 |
| 本机真实语料边界 | 7 PDF + 4 DOCX + 3 旧 `.doc`；`.doc` 经本机 LibreOffice 3/3 成功（外部后端不可用时仍按 E4151 拒收） |
| 黄金样本 | 全量中的逐字节可复现与 MANIFEST 校验通过；31 份历史 CRLF 污染不在本轮改动范围 |

本机证据文件（均位于 `work/`，不入库）：`work/_ch489_gate_final2.xml`、
`work/_ch489_full_final2.xml`、`work/real_samples/ch489_modes/{offline,server,summary.json,INDEX.md}`。

## 5. B/C 类登记与提醒

| 编号 | 事项 | 为什么仍需外部/人工 |
| --- | --- | --- |
| B-6 | Win11 域账户 / 受管终端的 ACL、`pwsh 7 -VerifyOnly`、计划任务常驻 | 本机 ACL/junction/XML 已验；域策略与 pwsh 7 缺失 |
| B-10 | 外置 U 盘 / SMB 网络卷、拔盘、并发 cron 的真实恢复 | 需要真实外置介质与人工操作 |
| B-13 | 真实 Word 模板样式联动人工抽查 | 模板本地保留，需人工确认文本框/编号/合并单元格 |
| B-15 | 非模板真实中文 PDF 的人工标题 ground truth | 现有 10 份仍是 Office 导出 PDF，recall 仍未达标 |
| B-16 | 真实注入语料与 `_attribute` 归因 | 需要安全语料与人工审阅 |
| B-21 | 跨进程产物锁的 60s 陈旧判定（长重建可能被误抢） | 需要超 60s 的真实重建/并发压测；建议下轮加心跳或 PID 存活判定 |
| B-22 | API 同步路径的 E4091 前置预检 | 当前仍先转换/package、再由 `save_ir` 拒绝并从 DB 重建；改前置会改变 single-file 的 HTTP 409 vs 202 契约，需决策 |
| B-23 | API `paths.out` 之外的其他进程级路径（logs/tmp/reports）运行期变更 | 本轮只冻结 out；logs 写入器/旧 tmp 的迁移策略需目标机验证 |
| B-24 | `Store` 每线程 64MB cache + 256MB mmap 在高并发请求线程下的驻留 | 单用户负载无碍；需真实并发压测量化后再调 |
| B-25 | DOCX 段中分页符位置、页眉页脚正文抽取 | 现有实现按段后分页，复杂版式需真实语料人工确认 |
| B-26 | 浏览器端编辑器与队列面板的真实交互 | 本机无头/人工浏览器矩阵仍未纳入 CI |

**提醒（每批交付时重复）**：B-6/B-10 是目标机与外部介质；B-13/B-15/B-16 是真实业务语料与人工标注；
B-21/B-22/B-24 是性能/契约决策项；B-25/B-26 是复杂版式与浏览器人工验收。以上均未在本轮声称已验证。

## 6. 变更与回滚

- 版本：`PIPELINE_VERSION` 2.10.0→**2.11.0**（DOCX 单行/单列表格与链接转义改变产物）、
  `PLAN_VERSION` 3.24→**3.25**、`SCHEMA_VERSION` 14、`DB_VERSION` 9 不变。
- 回滚：门禁零页、分页归一、tmp 校验、watch 标记、drift doc_id、LLM 白名单均为局部逻辑；
  单行/单列表格与链接转义可通过回退 `s3_structure.analyze` / `s4_render._inline` 恢复旧产物；
  删前快照与 `paths.out` 冻结为数据安全改动，建议保留。
- 未创建提交、未推送、未触发远端 CI：这些属于本机以外动作，按纪律只登记并在交付提醒中列出。
