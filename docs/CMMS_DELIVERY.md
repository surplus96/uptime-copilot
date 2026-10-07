# 구조화 CMMS 작업지시서 연동

긴급 작업지시서가 승인되면 Copilot은 Atlas에 짧은 제목·요약을 생성하고, 부품별 조치 절차를 Tasks에 별도 등록한다. 증상·매뉴얼 출처와 합성 재고 정보는 각 항목의 메모에 기록한다. Parts 재고 소비는 자동 실행하지 않는다.

## 실행 조건

Atlas MCP에 `add-work-order-tasks` 및 `get-work-order-tasks` 도구가 있어야 한다. Copilot만 업데이트하면 기존 MCP는 Tasks 요청을 처리할 수 없으므로 두 프로젝트를 함께 배포한다.

```bash
cd /Users/surplus96/projects/atlas-mcp
docker compose up -d --build atlas-mcp
cd /Users/surplus96/projects/uptime-copilot
docker compose up -d --build backend frontend
```

`backend/.env`의 `CMMS_MCP_URL`, `CMMS_MCP_TOKEN`을 설정한다. 실제 Atlas 자산 ID를 확인한 경우 `CMMS_ASSET_MAP`에 JSON을 넣을 수 있다. 예: `CMMS_ASSET_MAP={"84":1}`. 기존 기본 매핑은 84번 설비 → Atlas 자산 1이다. 매핑이 없는 설비는 자산 없이 생성한다.

## 전송 상태와 재시도

전송 기록은 데이터 볼륨의 `pdm_telemetry.db` 안 `cmms_deliveries`에 저장된다. 원본 승인 내용, thread, 목적 CMMS로 전송을 식별한다. 숫자 `work_order_id`는 API 후속 호출용이며 `display_id`는 화면의 WO 코드다.

| 상태 | 의미 | 처리 |
|---|---|---|
| `sent` | 작업지시서와 Tasks·메모 저장 검증 완료 | 반복 전송은 기존 성공을 반환 |
| `partial_tasks` | 작업지시서 생성 확인, Tasks 등록 미완료 | 화면의 점검 항목 등록 재시도 버튼 |
| `created` | 생성된 ID 저장, Tasks 등록 진행 중 또는 중단 | 완료된 승인 건은 같은 retry API 사용 가능 |
| `creating` / `creation_unknown` | 생성 진행 중 또는 응답 유실로 결과 미확인 | Atlas에서 생성 여부 확인; 자동 재생성하지 않음 |
| `blocked_missing_tasks` | 전달할 절차가 없음 | 매뉴얼 구성 확인 |

재시도 API:

```http
POST /agent/cmms/retry
Content-Type: application/json

{"thread_id":"기존 승인 스레드 ID"}
```

서버는 해당 그래프가 완료된 긴급 승인 건인지와 생성된 Atlas ID가 있는지 검사한다. 이후 기존 작업지시서에 Tasks만 등록하며 Slack이나 승인 처리를 반복하지 않는다. 상태 조회는 기존 `/agent/status/{thread_id}`로 할 수 있다. 전송 원문·점검 결과는 실제 모델 측정값과 합성 부품 정보를 구분해서 해석한다.

신규 승인 건부터 적용된다. 기존 Atlas 작업지시서는 자동 변경하지 않으며 생성 결과 미확인의 자동 복구·전체 매뉴얼 파일 첨부·실제 부품 소비는 별도 구현 범위다.
