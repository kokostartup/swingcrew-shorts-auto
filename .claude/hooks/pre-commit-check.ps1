# Pre-Commit Hook: 비밀키 패턴 + .env 스테이징 차단
# git commit 직전 (Bash 도구로 git commit 호출 시) 검사.

$ErrorActionPreference = "Stop"

# Bash tool 호출이고, 명령어가 git commit이 아니면 무조건 통과
try {
    $stdin = [Console]::In.ReadToEnd()
    if ($stdin) {
        $payload = $stdin | ConvertFrom-Json
        $cmd = $payload.tool_input.command
        if ($cmd -and ($cmd -notmatch 'git\s+commit')) {
            exit 0
        }
    }
} catch {
    # JSON 파싱 실패 시 그냥 검사 진행
}

# git 저장소 아니면 skip
$inRepo = git rev-parse --is-inside-work-tree 2>$null
if ($LASTEXITCODE -ne 0 -or $inRepo -ne "true") { exit 0 }

$staged = git diff --cached --name-only 2>$null

# .env 스테이징 차단 (단 .env.example 같은 템플릿은 허용)
foreach ($file in $staged) {
    if ($file -match '(^|/)\.env($|\.(?!example$|template$|sample$))') {
        [Console]::Error.WriteLine("BLOCKED: .env file is staged for commit ($file)")
        exit 2
    }
}

# 비밀키 패턴 스캔
$patterns = @(
    'sk-[a-zA-Z0-9]{20,}',
    'AIza[0-9A-Za-z_-]{35}',
    'ghp_[a-zA-Z0-9]{36}',
    'xoxb-[0-9]+-[a-zA-Z0-9]+'
)

$diff = git diff --cached 2>$null
if (-not $diff) { exit 0 }

foreach ($pattern in $patterns) {
    if ($diff -match $pattern) {
        [Console]::Error.WriteLine("BLOCKED: Secret key pattern detected: $pattern")
        exit 2
    }
}

exit 0
