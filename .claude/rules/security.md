# Security Rules

## API Key Management

- 모든 비밀키는 `.env` 파일에만 저장
- 코드에서는 `pydantic-settings`로만 로드 (절대 직접 `os.environ` 접근 금지)
- 다음 패턴은 절대 코드/주석/문서에 등장 금지:
  - `sk-[a-zA-Z0-9]{20,}` (OpenAI 키)
  - `AIza[0-9A-Za-z_-]{35}` (Google API 키)
  - `ghp_[a-zA-Z0-9]{36}` (GitHub PAT)
  - `xoxb-[0-9]+-[a-zA-Z0-9]+` (Slack 봇 토큰)

## Git Commit

- `.env` 파일은 절대 commit 금지 (`.gitignore`에 명시됨)
- commit 전 `git diff --cached`로 비밀키 패턴 확인
- 영상/오디오 원본은 commit 금지

## Subprocess Safety

- FFmpeg/yt-dlp 호출 시 사용자 입력은 반드시 sanitize
- `shell=True` 사용 금지
- 명령어는 list 형식으로 전달 (`subprocess.run(["ffmpeg", "-i", path])`)

## OAuth Token Storage

- YouTube OAuth refresh token은 `data/` 폴더에 저장 (`.gitignore` 포함)
- 토큰 만료 자동 갱신
- 만료된 토큰은 즉시 삭제
