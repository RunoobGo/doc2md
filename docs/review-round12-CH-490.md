# Doc2md 第十二轮（遗留项本机批次，CH-490）

> 日期：2026-10-18
> 范围：只执行上一轮登记中**本机可实施**的 B-21 / B-22 / B-23 / B-24 / B-26
> 原则：功能全面、性能优先；外部介质、目标机、人工标注仍只登记

## 1. 落地项

| 编号 | 发现 | 处置 |
| --- | --- | --- |
| B-21 | 跨进程产物锁按 60s mtime 判陈旧，长重建会被误抢；旧持有者释放时可能删掉新锁 | 持锁期间心跳刷新 mtime；锁文件带唯一 token，释放只清自己的锁；新增并发回归（存活心跳不抢锁、无心跳旧锁可回收） |
| B-22 | API 同步路径先转换/打包，save_ir 才撞 E4091，白做一次解析并短暂覆盖旧 zip | `precheck_edits=True` 前移到转换前；冲突仍返回 `conflict/202`，批量 `_convert_one` 增加 `conflict` 分类；带 Idempotency-Key 的请求先复用既有作业，保持 CH-475 重放语义 |
| B-23 | `paths.logs` 在启动时绑定写入器，运行期变更无效且 `/settings` 与实际位置不一致 | 加入运行期不可变键；reload 钉回当前 logs；新增回归 |
| B-24 | 每请求线程连接各 64MB 页缓存 + 256MB mmap，8GB 目标机高并发有驻留风险 | 50MB 扫描表实测：8/16/32 线程 **473/867/2144MB**；改 8MB cache + mmap=0 后 16 线程并发扫描 **807MB → 164MB**（mmap 64MB → 0，同测试程序实测）。保留 OS page cache |
| B-26 | 编辑器只有静态 HTML 断言，无真实浏览器交互证据 | 启动本机 serve + 浏览器自动化：载入 4 块、队列 done、编辑 b3→rev2、检索 1 命中（fts）、审计显示 rev1→2；同时发现并修复“保存后 textarea 已更新但元信息仍显示 rev1”的 UI 缺陷 |

## 2. 验证

| 项目 | 结果 |
| --- | --- |
| `pytest -m gate` | **402 项 = 401 passed / 1 platform skip（48.35s）** |
| 全量 `pytest` | **998 项 = 990 passed / 8 platform skips（125.11s）** |
| `ruff check .` | All checks passed |
| `doc2md doctor` | exit 0，`pipeline 2.12.0` |
| 综合语料 | **21/21 通过** |
| 浏览器实机 | 载入/编辑/保存/重取/检索/审计/队列计数全部通过；服务测试后已停止 |
| 幂等重放 | 带 Idempotency-Key 的已编辑文档重投仍返回既有 done 作业，不触发冲突 |

B-24 测量命令为本地临时脚本（`work/_b24_measure/`，workspace 已忽略）；数字是 Windows 11 / Python 3.12 本机口径，不是 Win11 目标机最终基准。

## 3. 仍未闭环

- **B-6**：域账户/受管终端 ACL、`pwsh 7 -VerifyOnly`、常驻任务真机。
- **B-10**：外置 U 盘/SMB 卷、拔盘、并发 cron 恢复。
- **B-13 / B-15 / B-16**：真实模板人工抽查、非模板中文 PDF 标题 ground truth、真实注入语料。
- **B-25**：DOCX 段中分页符位置与页眉页脚抽取；需要版面模型/真实版式样本，留待独立批次。
- **B-19 / B-17**：大文档 PATCH 的 O(全文) 重渲染、图片型 DOCX 媒体驻留，仍属架构项。

**提醒**：以上外部/人工项未在本轮声称完成；每批交付继续提醒。

## 4. 版本与回滚

- `PIPELINE_VERSION` 2.11.0→**2.12.0**（API 前置冲突/幂等预检、运行期路径契约、SQLite 驻留策略与编辑器修订显示）、`PLAN_VERSION` 3.25→**3.26**；`SCHEMA_VERSION` 14、`DB_VERSION` 9 不变。
- 回滚：锁心跳/token 可退回固定 60s；precheck 可退回转换后冲突路径；cache/mmap 为连接初始化两行；编辑器修复为两个 DOM 选择点。
