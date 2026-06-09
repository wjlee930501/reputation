"""Cloud Run worker/beat용 경량 헬스 HTTP 서버.

Cloud Run '서비스'는 컨테이너가 $PORT에 리슨해야 revision이 ready로 전환된다.
Celery worker/beat는 HTTP를 제공하지 않으므로 이 사이드 프로세스가 200을 응답한다.
프로세스 생존 = 컨테이너 정상으로 간주하며, 태스크 수준 실패는 Slack 알림과
Cloud Logging으로 관측한다 (docker-entrypoint.sh에서 celery와 함께 기동).
"""
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server 인터페이스
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):  # noqa: A002 — 요청 로그 소음 제거
        return


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    logger.info("worker health server listening on :%d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
