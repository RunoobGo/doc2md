param(
    [Parameter(Mandatory=$true)][string]$Docx,
    [Parameter(Mandatory=$true)][string]$Pdf
)
# 单份 Word 导出；批量时由调用方逐份启动并设置超时，避免 COM 模板对话框挂起。
$ErrorActionPreference = 'Stop'
$word = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.ScreenUpdating = $false
    try { $word.Options.UpdateFieldsAtPrint = $false } catch {}
    try { $word.Options.UpdateLinksAtPrint = $false } catch {}
    $doc = $word.Documents.Open($Docx, $false, $true)
    $doc.ExportAsFixedFormat($Pdf, 17)
    $doc.Close($false)
    Write-Output 'pdf_ok'
} catch {
    Write-Output ('pdf_fail: ' + $_.Exception.Message)
} finally {
    if ($word -ne $null) { $word.Quit() }
}
