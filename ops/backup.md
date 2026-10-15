# Doc2md 备份加密与轮出（NFR-18 / CH-406，附录 C-3）

## 介质分级

| 位置 | 形态 | 权限 |
| --- | --- | --- |
| `work/reports/backups/state-*.sqlite` | 本地快照，**可明文** | 目录 700（CH-437 起由备份路径实际 chmod） |
| 外置卷 `ops.backup_external_dir` | `*.enc` 容器，**强制加密** | 文件 600 |

外置卷未配置时，`cron --weekly` 的 external_backup 步骤记为 `skipped`，不判失败（CH-433）；
配置了外置卷但环境变量 `DOC2MD_BACKUP_PASSPHRASE` 缺失时判 `ok=False` ——
**宁可不备份，也不落明文**。

## 方案二选一

本仓库落地 **AES-256-GCM 容器**（`doc2md/ops/backup.py`，cryptography 实现，跨平台、脚本友好）：

```
容器格式（v2，CH-461）：
  b"D2MBAE2" | salt(16B 随机) | chunk_size(4B) | total(8B)
  + 每块 nonce(12B) + AESGCM(scrypt(n=2**14, r=8, p=1), AAD=魔数+total+序号)
  分块 4MB 流式加解密（驻留峰值 ≈ 一块）；历史 v1（b"D2MBAE1" + 固定盐 +
  整库单块）只读兼容、不再生产——旧 .enc 副本仍可恢复，但旧版程序解不了新容器。
口令来源： 仅环境变量 DOC2MD_BACKUP_PASSPHRASE（不写 plist、不入库、不入日志）
```

macOS 部署可改用加密 sparsebundle（`doc2md.ops.backup.hdiutil_recipe()` 输出即此命令）：

```bash
hdiutil create -encryption AES-256 -stdinpass -type SPARSEBUNDLE \
  -size 10g -fs APFS ~/doc2md/backups/secure.sparsebundle
```

两种介质只选其一写入 README 与运维脚本；混用不会冲突，但恢复演练口径不同。

## 操作序列

```bash
# 每日 / 每周（launchd 触发，见 ops/launchd/*.plist）
doc2md cron --daily
DOC2MD_BACKUP_PASSPHRASE='…' doc2md cron --weekly

# 恢复演练（DoD 第 11 条，一条命令跑完闭环）
DOC2MD_BACKUP_PASSPHRASE='…' doc2md backup drill            # 退出码 0=成功 4=失败
DOC2MD_BACKUP_PASSPHRASE='…' doc2md backup drill --keep      # 留存沙箱取证

# 真恢复（从外置卷单个容器解密回工作根）
python3 - <<'PY'
from pathlib import Path
from doc2md.ops.backup import BackupPlan
import os
p = os.environ["DOC2MD_BACKUP_PASSPHRASE"]
plan = BackupPlan(local_dir=Path("work/reports/backups"), external_dir=Path("/Volumes/doc2md-backup"))
plan.restore(Path("/Volumes/doc2md-backup/state-XXXX.sqlite.enc"), Path("work/state.sqlite"), p)
PY
sqlite3 work/state.sqlite "PRAGMA integrity_check;"   # 期望 ok
```

`backup drill`（CH-456 / T7）在 `work/tmp/drill-<ts>/` 隔离沙箱内依次执行：
**copy_live → snapshot → external_encrypt → simulate_loss → rotate → restore →
integrity_check → compare**。线上 `state.sqlite` 只被只读拷贝，演练结束即清沙箱；
任一步失败整体判失败（退出码 4），不落「看起来成功」的报告。报告只含表名、
行数与 sha256 指纹前缀（NFR-20），口令只走 `DOC2MD_BACKUP_PASSPHRASE`。

## 密钥纪律（NFR-20）

- 密钥**不入库**、**不随备份走**、**不写入任何日志 / 日报 / 事件流**；
- 托管方式自定（macOS Login Keychain / 密码管理器 / 离线口令卡），写入部署记录；
- 口令丢失 = 外置副本永久不可读，此时唯一恢复面是 `work/` 内的本地快照（≤14 天）。

## 轮出语义

`BackupPlan.rotate()` 按文件 mtime 删除超过 `ops.backup_rotation_days`（默认 14 天）的本地与外置副本，
实现 FR-128「已删数据 ≤14 天后从全部副本自然轮出」。删除级联本身（`DELETE /docs/{id}`）
会同步清除 out 产物、IR、事件、revision 与编辑审计，只保留不含正文的删除留痕。
