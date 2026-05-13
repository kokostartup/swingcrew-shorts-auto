# Pre-Bash Hook: 위험한 명령어 차단
# Claude Code가 Bash 도구 호출 시 stdin으로 JSON을 전달.
# tool_input.command 검사 후 위험하면 exit 2 (차단), 아니면 exit 0.

$ErrorActionPreference = "Stop"

try {
    $stdin = [Console]::In.ReadToEnd()
    if (-not $stdin) { exit 0 }
    $payload = $stdin | ConvertFrom-Json
    $cmd = $payload.tool_input.command
} catch {
    exit 0
}

if (-not $cmd) { exit 0 }

# rm -rf 차단 (outputs/, data/, /tmp/ 외부)
if ($cmd -match 'rm\s+-rf?\s+') {
    if ($cmd -notmatch 'rm\s+-rf?\s+(outputs|data|/tmp|\.\\outputs|\.\\data)') {
        [Console]::Error.WriteLine("BLOCKED: rm -rf outside outputs/, data/, /tmp/ requires manual approval")
        exit 2
    }
}

# .env 삭제/이동 차단
if ($cmd -match '(rm|mv|del|Remove-Item|Move-Item)\s+[^\r\n]*\.env(\s|$)') {
    [Console]::Error.WriteLine("BLOCKED: .env file modification not allowed")
    exit 2
}

# git push --force 차단
if ($cmd -match 'git\s+push\s+[^\r\n]*--force') {
    [Console]::Error.WriteLine("BLOCKED: force push not allowed")
    exit 2
}

# curl | sh / iwr | iex 차단 (원격 코드 실행)
if ($cmd -match '(curl|iwr|Invoke-WebRequest|wget)[^\r\n]*\|\s*(sh|bash|iex|Invoke-Expression)') {
    [Console]::Error.WriteLine("BLOCKED: remote-pipe execution not allowed")
    exit 2
}

exit 0
