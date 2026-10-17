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
容器格式（v2，CH-461；头绑定 AAD 为 CH-469）：
  b"D2MBAE2" | salt(16B 随机) | chunk_size(4B) | total(8B)
  + 每块 nonce(12B) + AESGCM(scrypt(n=2**14, r=8, p=1), AAD=整段 35B 容器头 + 序号)
  ↑ AAD 绑头本身意味着改 salt / 改 chunk_size / 改 total 会让全部块认证失败，
    防的是「换盐重放、拆块重组、改头诱导解密侧按另一种切块读」这类攻击；
    早期口径只绑魔数+total+序号（头可篡改），解密侧首块探测口径、向后兼容旧容器；
    两种口径都须由口令未知的同一把密钥认证通过，探测不构成降级通道。
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
integrity_check → compare**。线上 `state.sqlite` 只被只读拷贝，默认演练结束即清沙箱，
`--keep` 保留取证（CH-476 / R7-OPS-1：`drill-*` 目录永久豁免于 tmp 周期清扫与
`cron --daily` 清理，否则「留存取证」的承诺会在下一次无人值守作业里失效）；
任一步失败整体判失败（退出码 4），不落「看起来成功」的报告。报告只含表名、
行数与 sha256 指纹前缀（NFR-20），口令只走 `DOC2MD_BACKUP_PASSPHRASE`。

## 密钥纪律（NFR-20）

- 密钥**不入库**、**不随备份走**、**不写入任何日志 / 日报 / 事件流**；
- 托管方式自定（macOS Login Keychain / 密码管理器 / 离线口令卡），写入部署记录；
- 口令丢失 = 外置副本永久不可读，此时唯一恢复面是 `work/` 内的本地快照（≤14 天）。

## 轮出语义

`BackupPlan.rotate()` 删除超过 `ops.backup_rotation_days`（默认 14 天）的本地与外置副本，
实现 FR-128「已删数据 ≤14 天后从全部副本自然轮出」。两条口径（CH-469 / O-2）：

- **名字闸门**：只处理匹配 `state-<YYYYMMDDTHHMMSSZ>.sqlite[.enc]` 的文件（`backup.py:42` 的 `_SNAPSHOT_RE`）。备份目录是最容易被
  「顺手放一下」的目录，早期版本按 `glob("*")` + mtime 判定，会把用户放在同一目录的
  无关过期文件一并删掉；现在不是本工具形态的一律不碰。
- **过期判据取更早者**：文件名内嵌的生成时刻与磁盘 mtime 两者中**更早**的那个超过保留期即删。
  取生成时刻是因为跨卷拷贝会把 mtime 改成拷贝当天（副本看起来"很新"却已超期）；
  取 mtime 是因为老副本可能没有可解析的时刻，此时退回文件系统信号。

删除级联本身（`DELETE /docs/{id}`）会同步清除 out 产物、IR、事件、revision 与编辑审计，
只保留不含正文的删除留痕。
