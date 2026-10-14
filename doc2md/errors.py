"""错误码体系（方案 附录 D；上游锚点 v2.1 §7.8，v2.4 仅增补 E4133，不改冻结段位）。

冻结纪律（方案 5.2 / 上游 v2.4 §10-4）：既有错误码段位不可变更，本期只新增：
- E4133 目录文件数超限（FR-129）
- E4251 版本冲突（本期赋予真实语义：乐观锁命中，ADR-28）
"""


class Doc2mdError(Exception):
    code = "E5000"
    http = 500

    def __init__(self, message: str = "", detail: dict | None = None):
        super().__init__(message or self.code)
        self.message = message or self.code
        self.detail = detail or {}

    def to_body(self) -> dict:
        body = {"code": self.code, "message": self.message}
        if self.detail:
            body["detail"] = self.detail
        return body


class ApiError(Doc2mdError):
    """HTTP 层错误。"""


# --- 4xx：输入安全 / 契约（v2.1 段位沿用，本期新增 4133）-------------------
class E4001_BadRequest(ApiError):
    code, http = "E4001", 400


class E4003_FileNameUnsafe(ApiError):
    """消毒后仍不可安全落库（FR-127 红线：消毒失败即拒收，不设降级）。"""
    code, http = "E4003", 400


class E4011_Unauthorized(ApiError):
    code, http = "E4011", 401


class E4031_Forbidden(ApiError):
    """角色不足（NFR-9）。"""
    code, http = "E4031", 403


class E4041_NotFound(ApiError):
    code, http = "E4041", 404


class E4131_TooLarge(ApiError):
    code, http = "E4131", 413


class E4133_DirTooWide(ApiError):
    """目录文件数 / 深度超限（FR-129，本期新增）。"""
    code, http = "E4133", 413


class E4151_UnsupportedType(ApiError):
    """magic 校验不通过：非文本型 PDF/DOCX。"""
    code, http = "E4151", 415


class E4221_GateReject(ApiError):
    """S1.5 体检门禁拒收（含页级明细）。"""
    code, http = "E4221", 422


class E4251_RevisionConflict(ApiError):
    """乐观锁冲突（ADR-28）：响应附 current_revision 供客户端自愈。"""
    code, http = "E4251", 425


# --- 5xx：内部 ----------------------------------------------------------------
class E5031_StorageError(Doc2mdError):
    code, http = "E5031", 500


class E5041_LlmUnavailable(Doc2mdError):
    code, http = "E5041", 503



# --- 告警码（非 HTTP，写入产物 meta.warnings 与报告）------------------------
WARN_TABLE_ACROSS_SHARD = "W1043"        # 跨片表格不合并（FR-126 ④）
WARN_SHARD_NOT_MERGED = "W1044"          # 分片转换说明（报告口径；CH-456 起真实写入）

CODE_INDEX = {
    cls.code: cls
    for cls in [
        E4001_BadRequest, E4003_FileNameUnsafe, E4011_Unauthorized, E4031_Forbidden,
        E4041_NotFound, E4131_TooLarge, E4133_DirTooWide,
        E4151_UnsupportedType, E4221_GateReject, E4251_RevisionConflict,
        E5031_StorageError, E5041_LlmUnavailable,
    ]
}
