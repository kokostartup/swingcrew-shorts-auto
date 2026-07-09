"""1회용: Buffer customScheduled로 등록한 P021 + B012 TikTok post 삭제.

Buffer 무료 플랜 예약 한도(10개) 회수 + 새 흐름 (slot 시각 shareNow)으로 전환.
post_id는 이전 작업 log에서 직접 모음.
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.integrations.buffer import _graphql

POSTS = {
    "26-P021-S01": "6a38c1a4e01bad5f269365b7",
    "26-P021-S02": "6a38c1a5e66a013484e32e25",
    "26-P021-S03": "6a38c1a70345e73a0fc94c97",
    "26-P021-S04": "6a38c1a80ec827fbea1a11a7",
    "26-P021-S05": "6a38c1aac07cc7a21e8b0d5b",
    "26-P021-S06": "6a38c1ace66a013484e32f16",
    "26-B012-S01": "6a38d5b60ec827fbea1b28a4",
    "26-B012-S02": "6a38d5c20ec827fbea1b28f6",
    "26-B012-S03": "6a38d5cde01bad5f26947441",
    "26-B012-S04": "6a38d5dac07cc7a21e8c16d9",
    # 26-B012-S05: Buffer 응답 post_id 빈 값 — 등록 안 됐을 가능성, skip
}

MUTATION = (
    "mutation DeletePost($input: DeletePostInput!) {"
    " deletePost(input: $input) {"
    " __typename"
    " ... on DeletePostSuccess { post { id } }"
    " ... on VoidMutationError { message }"
    " }"
    "}"
)


def main() -> int:
    deleted = 0
    for iid, pid in POSTS.items():
        try:
            data = _graphql(MUTATION, {"input": {"id": pid}})
            result = data["deletePost"]
            if result.get("__typename") == "MutationError":
                print(f"  {iid} ({pid[:12]}..): FAIL {result.get('message')}")
                continue
            deleted += 1
            print(f"  {iid} ({pid[:12]}..): deleted")
        except Exception as e:
            print(f"  {iid} ({pid[:12]}..): EXCEPTION {e}")
    print(f"\ndeleted: {deleted}/{len(POSTS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
