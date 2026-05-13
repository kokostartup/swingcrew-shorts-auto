# Post-Edit Hook: Python 파일 수정 후 자동 ruff 적용
# tool_input.file_path가 .py로 끝나면 ruff check --fix + format.

$ErrorActionPreference = "SilentlyContinue"

try {
    $stdin = [Console]::In.ReadToEnd()
    if (-not $stdin) { exit 0 }
    $payload = $stdin | ConvertFrom-Json
    $filePath = $payload.tool_input.file_path
} catch {
    exit 0
}

if (-not $filePath) { exit 0 }
if (-not ($filePath -like '*.py')) { exit 0 }
if (-not (Test-Path $filePath)) { exit 0 }

# uv가 PATH에 있을 때만 실행. 없으면 조용히 skip (Phase 0 환경 호환).
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    $uvCmd = Get-Command "python" -ErrorAction SilentlyContinue
    if ($uvCmd) {
        & python -m uv run ruff check --fix $filePath 2>&1 | Out-Null
        & python -m uv run ruff format $filePath 2>&1 | Out-Null
    }
    exit 0
}

& uv run ruff check --fix $filePath 2>&1 | Out-Null
& uv run ruff format $filePath 2>&1 | Out-Null

exit 0
