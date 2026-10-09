# /// script
# requires-python = ">=3.11"
# dependencies = ["redis"]
# ///
"""Deterministic, internal-only provider and callback boundary for Task 16."""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

import redis


broker = redis.Redis.from_url(
    os.environ["REDIS_URL"], decode_responses=True, socket_timeout=5, socket_connect_timeout=5
)


def record(kind: str, path: str, body: bytes = b"") -> None:
    broker.rpush(
        "rehearsal:provider_receipts",
        json.dumps(
            {"kind": kind, "path": path, "bodySha": __import__("hashlib").sha256(body).hexdigest()},
            separators=(",", ":"),
        ),
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "ReputationRehearsalFixture/1"

    def reply(self, code: int, payload: dict[str, Any] | bytes, content_type: str) -> None:
        encoded = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except BrokenPipeError:
            self.log_message("client disconnected during deterministic fixture response")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        record("GET", self.path)
        if self.path == "/health":
            self.reply(200, {"status": "ok", "provenance": "TEST_ONLY"}, "application/json")
            return
        if self.path.startswith("/source/"):
            html = (
                "<!doctype html><html lang='ko'><head><title>대한의학회 검사 안내</title></head>"
                "<body><main><h1>대장내시경 검사 안내</h1><p>검사 준비와 회복은 의료진의 "
                "개별 안내에 따라야 합니다. 증상과 병력을 확인한 뒤 검사 계획을 정합니다.</p>"
                "</main></body></html>"
            ).encode()
            self.reply(200, html, "text/html; charset=utf-8")
            return
        broker.rpush("rehearsal:unexpected_requests", f"GET {self.path}")
        self.reply(503, {"error": "UNEXPECTED_TEST_BOUNDARY"}, "application/json")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        record("POST", self.path, body)
        if self.path == "/api/v1/images":
            broker.incr("rehearsal:provider_calls")
            self.reply(503, {"error": "TEST_ONLY_PROVIDER_UNAVAILABLE"}, "application/json")
            return
        if self.path == "/api/v1/chat/completions":
            broker.incr("rehearsal:provider_calls")
            request_payload = json.loads(body or b"{}")
            tool_names = [
                str(tool.get("function", {}).get("name") or "")
                for tool in request_payload.get("tools", [])
                if isinstance(tool, dict)
            ]
            if "report_review" in tool_names:
                arguments = json.dumps(
                    {
                        "decision": "PASS",
                        "confidence": 0.99,
                        "findings": [],
                        "summary": "테스트 후보는 승인된 병원 사실과 의료 안전 기준에 부합합니다.",
                    },
                    ensure_ascii=False,
                )
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "task16-review-call",
                            "type": "function",
                            "function": {"name": "report_review", "arguments": arguments},
                        }
                    ],
                }
                finish_reason = "tool_calls"
            elif "emit_article" in tool_names:
                paragraph = (
                    "리허설 바른의원은 서울 중구에서 방문 전 진료시간과 준비 사항을 안내합니다. "
                    "김바른 원장은 환자의 질문과 현재 상태를 먼저 확인하고 필요한 설명을 쉬운 말로 제공합니다. "
                    "개인의 증상과 병력에 따라 안내가 달라질 수 있으므로 불편이 지속되면 의료진과 상담해 주세요. "
                )
                body_text = "\n\n".join(
                    f"## {heading}\n{paragraph * 8}"
                    for heading in ("방문 전 확인", "진료 당일", "상담할 내용", "진료 후 안내")
                )
                arguments = json.dumps(
                    {
                        "title": "방문 전 확인하는 진료 안내",
                        "body": body_text,
                        "meta_description": "서울 중구 리허설 바른의원의 방문 전 준비와 진료 당일 확인 사항을 안내합니다.",
                        "references": [],
                        "faq_question": None,
                        "faq_answer_summary": None,
                    },
                    ensure_ascii=False,
                )
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "task16-article-call",
                            "type": "function",
                            "function": {"name": "emit_article", "arguments": arguments},
                        }
                    ],
                }
                finish_reason = "tool_calls"
            else:
                message = {
                    "role": "assistant",
                    "content": "근거를 확인한 테스트 전용 콘텐츠 응답입니다.",
                }
                finish_reason = "stop"
            payload = {
                "id": "test-only-chat",
                "model": str(request_payload.get("model") or "test-only/model"),
                "choices": [
                    {
                        "message": message,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16},
            }
            self.reply(200, payload, "application/json")
            return
        if self.path == "/revalidate":
            if self.headers.get("x-revalidate-secret") != "test-only-revalidation":
                self.reply(403, {"error": "BAD_TEST_SECRET"}, "application/json")
                return
            paths = json.loads(body)["paths"]
            if not paths or not all(
                path in {"/", "/sitemap.xml", "/llms.txt"} or path.startswith("/rehearsal-")
                for path in paths
            ):
                raise AssertionError(f"unexpected revalidation paths: {paths!r}")
            broker.rpush("rehearsal:callbacks", body)
            broker.set("rehearsal:callback_entered", "1")
            deadline = time.monotonic() + 120
            while broker.get("rehearsal:block_callback") == "1":
                if time.monotonic() > deadline:
                    self.reply(504, {"error": "TEST_ONLY_CALLBACK_TIMEOUT"}, "application/json")
                    return
                time.sleep(0.1)
            broker.incr("rehearsal:callback_accepted")
            if broker.get("rehearsal:forward_revalidation") == "1":
                forwarded = urllib_request.Request(
                    "http://site-new:3000/api/revalidate",
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "x-revalidate-secret": "test-only-revalidation",
                    },
                )
                try:
                    with urllib_request.urlopen(forwarded, timeout=30) as response:
                        forwarded_body = response.read()
                        if response.status == 200:
                            broker.incr("rehearsal:revalidation_forwarded")
                        self.reply(response.status, forwarded_body, "application/json")
                except urllib_error.HTTPError as exc:
                    self.reply(exc.code, exc.read(), "application/json")
                except urllib_error.URLError as exc:
                    self.reply(
                        503,
                        {"error": "SITE_REVALIDATE_UNAVAILABLE", "detail": str(exc.reason)},
                        "application/json",
                    )
                return
            self.reply(200, {"revalidated": True, "provenance": "TEST_ONLY"}, "application/json")
            return
        if self.path in {"/slack", "/indexnow", "/delivery"}:
            broker.incr(f"rehearsal:capture:{self.path[1:]}")
            self.reply(200, {"captured": True, "provenance": "TEST_ONLY"}, "application/json")
            return
        broker.rpush("rehearsal:unexpected_requests", f"POST {self.path}")
        self.reply(503, {"error": "UNEXPECTED_TEST_BOUNDARY"}, "application/json")


ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
