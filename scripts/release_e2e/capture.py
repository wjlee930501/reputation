"""Wire-compatible synthetic AI providers and HTTPS Slack sink; isolated Docker only."""

import base64
import io
import json
import ssl
import threading
import time
from collections import Counter
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

ROOT = Path("/evidence")
COUNTS = Counter()
LOCK = threading.Lock()
image = Image.new("RGB", (640, 360), (216, 228, 225))
buf = io.BytesIO()
image.save(buf, format="PNG")
PNG = buf.getvalue()
POLICY = dict(
    has_text=False,
    has_logo=False,
    has_recognizable_people=False,
    impersonates_real_clinic=False,
    topic_relevant=True,
)
NAME = "검증 한빛정형외과"
ANSWER = f"서울 강남구에서 진료 안내를 확인할 병원으로 {NAME}가 있습니다. 진료 전 공식 안내를 확인하세요."


def record(path, body, status):
    with LOCK:
        with (ROOT / "wire.jsonl").open("a") as out:
            out.write(
                json.dumps(
                    dict(path=path, body=body, status=status, time=time.time()),
                    ensure_ascii=False,
                )
                + "\n"
            )


def article(number):
    paragraph = (
        "서울 강남구에서 허리 통증으로 진료를 고민할 때는 증상이 시작된 시점과 불편한 동작을 기록해 두는 것이 도움이 됩니다. "
        f"{NAME} 검증 원장의 진료 안내는 환자가 자신의 상황을 이해하고 의료진과 상담할 수 있도록 구성합니다. "
        "이 글은 일반적인 정보이며 개인의 진단이나 치료를 대신하지 않습니다. 증상이 지속되거나 심해지면 의료진과 상담하세요. "
        "의료진에게 현재 복용 중인 약과 이전 검사 여부를 알려주세요. 검사와 치료의 필요성은 진찰 결과에 따라 달라질 수 있습니다. "
    )
    return dict(
        title=f"서울 강남구 허리 통증 진료 전에 무엇을 준비하나요? {number}",
        body="## 진료 전 준비\n\n" + "\n\n".join([paragraph] * 11),
        meta_description="서울 강남구 허리 통증 진료 전 질문과 준비 사항을 안내합니다.",
        faq_question="허리 통증 진료 전에 무엇을 준비하나요?",
        faq_answer_summary="증상이 시작된 시점과 불편한 동작, 복용 중인 약을 정리해 의료진과 상담하세요.",
        references=[
            dict(title="질병관리청 국가건강정보포털", url="https://health.kdca.go.kr/")
        ],
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, data, mime="application/json"):
        raw = (
            data
            if isinstance(data, bytes)
            else json.dumps(data, ensure_ascii=False).encode()
        )
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        # Local TLS/DNS substitute forwards ownership proof to the REAL Site.
        # It never fabricates the IndexNow key or a hospital page.
        if self.headers.get("Host", "").split(":")[0] == "e2e-clinic-0.site.example.test":
            upstream = HTTPConnection("qa-site", 8080, timeout=10)
            upstream.request("GET", self.path, headers={"Host": "e2e-clinic-0.site.example.test"})
            result = upstream.getresponse()
            raw = result.read()
            self.respond(
                result.status, raw, result.getheader("Content-Type", "text/plain")
            )
            upstream.close()
            return
        if self.path.startswith("/assets/"):
            # Serve the bytes actually persisted through the storage adapter, not
            # the generated fixture independently of the requested object key.
            from urllib.parse import urlsplit

            key = Path(urlsplit(self.path).path).name.removesuffix(".png")
            if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                return self.respond(404, b"not found", "text/plain")
            asset = ROOT / "assets" / key
            if not asset.is_file():
                return self.respond(404, b"not found", "text/plain")
            return self.respond(200, asset.read_bytes(), "image/png")
        return self.respond(200, {"counts": dict(COUNTS)})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.loads(raw) if raw else {}
        with LOCK:
            COUNTS[self.path] += 1
            n = COUNTS[self.path]
        status, result = 200, {}
        if self.path.startswith("/slack/"):
            status = 503 if n == 1 else 200
            record(self.path, body, status)
            return self.respond(
                status, b"unavailable" if status == 503 else b"ok", "text/plain"
            )
        if self.path == "/indexnow":
            record(self.path, body, 202)
            return self.respond(202, {})
        if self.path.endswith("/messages"):
            tool = body.get("tool_choice", {}).get("name")
            payload = (
                article(n)
                if tool == "emit_article"
                else dict(
                    decision="PASS",
                    confidence=0.99,
                    findings=[],
                    summary="합성 검수 응답",
                )
            )
            result = dict(
                id=f"msg_qa_{n}",
                type="message",
                role="assistant",
                model=body["model"],
                stop_reason="tool_use",
                usage=dict(input_tokens=100, output_tokens=100),
                content=[
                    dict(type="tool_use", id=f"tool_{n}", name=tool, input=payload)
                ],
            )
        elif self.path.endswith("/images/generations"):
            result = dict(
                created=int(time.time()),
                data=[dict(b64_json=base64.b64encode(PNG).decode())],
            )
        elif ":generateContent" in self.path:
            config = body.get("generationConfig", {})
            image_requested = "IMAGE" in config.get("responseModalities", [])
            policy_requested = config.get("responseMimeType") == "application/json"
            part = (
                dict(
                    inlineData=dict(
                        mimeType="image/png", data=base64.b64encode(PNG).decode()
                    )
                )
                if image_requested
                else dict(text=json.dumps(POLICY) if policy_requested else ANSWER)
            )
            result = dict(
                candidates=[
                    dict(content=dict(role="model", parts=[part]), finishReason="STOP")
                ],
                modelVersion=self.path.split("/models/")[-1].split(":")[0],
                usageMetadata=dict(
                    promptTokenCount=100, candidatesTokenCount=100, totalTokenCount=200
                ),
            )
        elif self.path.endswith("/responses"):
            result = dict(
                id=f"resp_{n}",
                object="response",
                status="completed",
                model=body["model"],
                output=[
                    dict(
                        type="message",
                        id=f"qa_{n}",
                        role="assistant",
                        status="completed",
                        content=[dict(type="output_text", text=ANSWER, annotations=[])],
                    )
                ],
                usage=dict(input_tokens=100, output_tokens=100, total_tokens=200),
            )
        elif self.path.endswith("/chat/completions"):
            is_image = isinstance(body.get("messages", [{}])[0].get("content"), list)
            payload = (
                POLICY
                if is_image
                else dict(
                    verdict="MATCHED",
                    matched_text=NAME,
                    mention_context=ANSWER,
                    mention_rank=1,
                    sentiment="neutral",
                )
            )
            result = dict(
                id=f"chat_{n}",
                object="chat.completion",
                model=body["model"],
                created=int(time.time()),
                choices=[
                    dict(
                        index=0,
                        finish_reason="stop",
                        message=dict(
                            role="assistant",
                            content=json.dumps(payload, ensure_ascii=False),
                        ),
                    )
                ],
                usage=dict(prompt_tokens=100, completion_tokens=100, total_tokens=200),
            )
        else:
            status, result = 400, dict(error="Unexpected synthetic provider endpoint")
        # Never record credentials or authorization headers.
        record(self.path, body, status)
        self.respond(status, result)


if __name__ == "__main__":
    http = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    tls = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
    tenant_tls = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(ROOT / "capture.crt"), str(ROOT / "capture.key"))
    tls.socket = ctx.wrap_socket(tls.socket, server_side=True)
    tenant_tls.socket = ctx.wrap_socket(tenant_tls.socket, server_side=True)
    threading.Thread(target=tenant_tls.serve_forever, daemon=True).start()
    threading.Thread(target=tls.serve_forever, daemon=True).start()
    http.serve_forever()
