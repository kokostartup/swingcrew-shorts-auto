# Stop Hook: 세션 종료 시 변경 파일 요약 저장
# data/session_log.md 에 타임스탬프 + git diff --name-only 추가.

$ErrorActionPreference = "SilentlyContinue"

$logFile = "data\session_log.md"
New-Item -ItemType Directory -Force -Path "data" | Out-Null

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

$inRepo = git rev-parse --is-inside-work-tree 2>$null
if ($LASTEXITCODE -eq 0 -and $inRepo -eq "true") {
    $changed = git diff --name-only HEAD 2>$null
    if (-not $changed) {
        $changed = git status --porcelain 2>$null
    }
    if (-not $changed) { $changed = "(no changes)" }
} else {
    $changed = "(not a git repository)"
}

$entry = @"
## Session: $timestamp

### Changed files
$($changed -join "`n")

---

"@

Add-Content -Path $logFile -Value $entry -Encoding UTF8

exit 0
