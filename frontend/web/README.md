# Uptime Copilot · React UI

기존 FastAPI와 연결되는 로컬 운영 UI. 이전 Streamlit은 `frontend/streamlit_app.py`에
유지하며 기본 Compose 동작을 변경하지 않는다.

## 개발

Node.js 24 LTS 권장. 먼저 저장소 README에 따라 데이터·모델을 준비하고 FastAPI를 8000에서
실행한다. 2단계부터 새 조회 API가 필요하므로 Docker 백엔드는 저장소 루트에서
`docker compose up -d --build backend`로 소스를 반영한다. 프론트 작업 폴더에서:

```bash
cd frontend/web
npm ci
npm run dev
```

http://127.0.0.1:5173 에서 확인한다. `/api/*`는 Vite가 기본
`http://127.0.0.1:8000/*`로 프록시한다. 다른 백엔드를 쓰려면:

```bash
BACKEND_URL=http://127.0.0.1:8001 npm run dev
```

이 주소는 Vite 서버 설정이며 브라우저에 비밀키를 전달하지 않는다. 백엔드 CORS 변경은
필요하지 않다. Vite는 기본적으로 루프백에만 바인딩한다.

## Docker

저장소 루트에서:

```bash
docker compose -f docker-compose.yml -f docker-compose.web.yml up -d --build web
```

http://127.0.0.1:3001 에서 확인한다. 다른 앱이 사용하는 3000 포트와 충돌하지 않도록
기본값을 3001로 두었다. 필요하면 `UPTIME_WEB_PORT=3002`처럼 실행 시 변경할 수 있다.
Nginx가 정적 파일을 제공하고 `/api/`를
`backend:8000`으로 전달한다. API Host 헤더는 백엔드의 `ALLOWED_HOSTS=backend`와 맞춘다.
Streamlit은 기존 명령으로 병행 실행할 수 있다. `web`만 중지하려면:

```bash
docker compose -f docker-compose.yml -f docker-compose.web.yml stop web
```

## 확인

```bash
npm test
npm run build
```

`npm run preview`는 production 정적 파일 확인용이며 API 프록시는 제공하지 않는다.
API 연동 확인은 개발 서버 또는 Docker 구성을 사용한다.

## 현재 범위와 데이터 의미

- 운영 개요, 이벤트 조회/필터/선택/스캔/완료/삭제, 진단·승인·반려·CMMS 부분 실패 재시도.
- 재고·매뉴얼·감사 로그·시뮬레이터를 기존 API에 연결한다.
- 설비 탐색과 센서 차트: 24시간/3일/7일/30일, 1/3/6/12/24시간 구간 평균. 결측을 연결하지 않고 최소·최대·표본 수를 제공한다.
- 승인 대기함: 서버의 최신 체크포인트를 읽어 닫았던 진단을 다시 연다. 목록 조회는 진단·전송을 수행하지 않는다.
- 이벤트는 페이지당 20건과 서버 필터를 지원한다. 전체 긴급·주의 집계는 페이지와 무관하다. 감사는 최대 200건이다.
- 재고 비교 차트는 합성 재고와 30/90일 누적 수요를 비교한다.
- 진단 URL의 `thread`로 서버 체크포인트를 복구한다. 페이지 이동 시 POST를 재전송하지 않는다.
- 타임아웃 후 결과 미확인 상태에서는 승인/반려를 잠근다. 상태 확인 후 서버가 승인 대기로
  응답했을 때만 결정 재시도를 허용한다. 새로고침 중 쓰기 요청은 취소를 보장하지 않는다.
- 탭의 sessionStorage에는 ID별 미확인 요청 종류만 보관한다. 진단 내용은 저장하지 않는다.
- 서버가 `running`으로 남은 경우 자동 취소/재시작하지 않는다. URL을 보존해 확인한다.
- 승인 결과와 Slack/CMMS 전송 결과를 분리하며, 이벤트 완료는 실제 정비 완료가 아니다.
- 부품 정보는 합성값, 수요는 추정치다. 모델 위험 점수를 고장 확률로 표시하지 않는다.
- 구조화 작업지시서 카드를 표시하고 문자열 원문을 함께 보존한다. 옛 체크포인트에 구조화 사실이 없으면 원문으로 대체한다.
- 외부 CDN·원격 폰트가 없다. 의존성 설치와 Docker 이미지 확보는 오프라인 배포 전에 완료한다.

전환 계획: [REACT_UI_MIGRATION_PLAN.md](../../docs/design/REACT_UI_MIGRATION_PLAN.md).

조회 API 계약: [UI_API_CONTRACT.md](../../docs/design/UI_API_CONTRACT.md).
