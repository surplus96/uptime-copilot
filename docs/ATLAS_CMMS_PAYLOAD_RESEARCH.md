# Atlas CMMS 매뉴얼 전달 구조 조사

조사일: 2026-10-07. 범위: 읽기 전용 조사와 전달 구조 제안. 연동 코드 수정 및 실제 작업지시서 생성·수정은 수행하지 않았다.

## 확인한 실행 환경과 근거

- Atlas backend: `intelloop/atlas-cmms-backend`, 실행 이미지 ID `sha256:4bce7a25ffce79a5be9a483fb1d743b832be205337e6b9ab72b7e67a7299cb0d`.
- Atlas frontend: `intelloop/atlas-cmms-frontend`, 실행 이미지 ID `sha256:e40247de2ce3092e5520befcc75825461309b740952efc681966061e53177e2f`.
- 로컬 Atlas 소스: `/Users/surplus96/projects/atlas-cmms`, upstream `https://github.com/Grashjs/cmms.git`, HEAD `869d648f028057d637ccb66b740f4499a097c2fc`.
- 로컬 MCP 소스: `/Users/surplus96/projects/atlas-mcp/src`.
- 실행 컨테이너의 `/app/my-spring-boot-app.jar`를 임시 폴더로 복사해 class constant pool과 필드를 분석했다. `TaskController`, `PartQuantityController`의 관련 API 경로, `TaskBaseDTO` 필드, `TaskType` enum이 아래 로컬 소스 구조와 일치했다.
- 이미지에 소스 commit 라벨이 없어 로컬 HEAD와 이미지 전체가 동일한 빌드인지 확정하지 않았다. 세부 처리 동작은 로컬 소스를 기준으로 설명한다. 실제 API 쓰기 테스트는 하지 않았다.
- 공개 OpenAPI 명세 조회는 `/v3/api-docs`에서 404, `/api/v3/api-docs`에서 401이었다. 명세를 확보했다고 주장하지 않는다.

## 현재 현상의 원인

`backend/agent/agent_service.py:work_order_node`는 이미 있는 구조화 상태를 하나의 문자열 `work_order`로 조립한다. 증상, 매뉴얼 근거, 조치사항, 긴급도, 재고·조달 정보가 모두 여기에 포함된다.

`backend/cmms_client.py`는 이 문자열 전체를 `_format_for_cmms`로 평탄화한 뒤 MCP `create-work-order`의 `description`으로 전달한다. 이 함수는 개행을 `·`, `▌`, `▸`로 바꾼다.

MCP `src/tools/createWorkOrder.ts`는 `POST /work-orders`만 호출한다. 입력 스키마에 tasks, checklist, 부품 연결, 파일 첨부가 없다.

Atlas frontend `frontend/src/content/own/WorkOrders/Details/WorkOrderDetails.tsx:553`은 `description`을 제목 바로 아래 `<Typography variant="h6">`로 표시한다. 제목 자체에 전체 매뉴얼이 들어간 것은 아니며, 헤더 영역의 설명에 전부 들어간 것이다. 같은 파일은 tasks가 비어 있으면 Tasks 영역을 렌더링하지 않는다.

## 권장 매핑

| 프로젝트 데이터 | Atlas 저장 대상 | 전달 방법 |
|---|---|---|
| 설비 번호, 사건 요약 | WorkOrder.title | `POST /work-orders` |
| 핵심 증상, 긴급도, 정지 권고 요약 | WorkOrder.description | 같은 생성 요청, 짧은 설명 |
| 긴급도·우선순위 | WorkOrder.priority | 현재는 HIGH 고정. P1/P2 등 내부 우선순위와의 매핑은 따로 정의 |
| 설비 연결 | WorkOrder.asset | REST는 `{"id": atlasAssetId}`, MCP는 `assetId` |
| 부품별 조치 절차 | TaskBase.label/taskType → Task | `PATCH /tasks/work-order/{numericId}` |
| 측정값·점검 결과 | Task.value | `PATCH /tasks/{taskId}`; 실제 결과를 받은 뒤 기록 |
| 항목별 증상·근거·조달 설명 | Task.notes | `PATCH /tasks/{taskId}`; 전체 매뉴얼 원문은 별도 보존 |
| 실제 사용하는 Atlas 부품 | PartQuantity | 별도 parts API, 아래 재고 차감 유의 |
| 전체 매뉴얼·출처 | Files 또는 별도 문서 보존 | 파일 등록 후 작업지시서 연결. 업로드 계약은 추가 확인 필요 |
| 예상 시간·담당자·기한 | WorkOrder의 전용 필드 | MCP의 estimatedDuration, primaryUserId, assignedToUserIds, dueDate 등 |

`Labors`는 실제 작업 시간·인건비, `Additional Costs`는 실제 추가 비용이다. 점검 문장이나 가상의 단가를 분류해서 넣는 영역으로 사용하지 않는다.

## 최소 전달 흐름과 요청 예시

아래 URL은 controller 기준 상대 경로다. Atlas API base URL이 `/api`로 끝나면 `/api/work-orders` 등으로 요청한다. ID들은 예시이며 실제로 매핑·조회한 값을 사용해야 한다.

1. MCP create-work-order로 짧은 제목·설명·자산을 전달한다. 현재 MCP는 assetId를 REST의 asset 객체로 변환한다.

```json
{
  "title": "설비 #90 전동기·회전자 긴급 점검",
  "description": "comp1 고장 이력과 comp2 오류 감지. P1, 정지 권고. 상세 절차는 작업 항목 참조.",
  "priority": "HIGH",
  "assetId": 123
}
```

2. 생성 결과의 `workOrderId`(숫자)를 읽는다. 화면 표시 ID `WO000010`과 혼동하면 안 된다. 현재 Copilot `_create_work_order`는 결과의 ID를 저장·반환하지 않아 후속 호출을 할 수 없다.

3. `PATCH /tasks/work-order/456`으로 절차 배열을 전달한다.

```json
[
  {"label": "[comp1] 01 전원 공급 상태 및 전압 안정성 확인", "taskType": "SUBTASK", "options": []},
  {"label": "[comp1] 02 모터 권선 절연저항 측정", "taskType": "SUBTASK", "options": []},
  {"label": "[comp2] 01 회전속도 로그의 이상 패턴 확인", "taskType": "SUBTASK", "options": []},
  {"label": "[comp2] 02 베어링 마모 상태 육안 및 진동 점검", "taskType": "SUBTASK", "options": []}
]
```

`SUBTASK`는 완료 여부를 기록하는 일반 절차에 적합하다. 타입은 SUBTASK/NUMBER/TEXT/INSPECTION/MULTIPLE/METER가 있다. 수치 기준이 없는 현재 매뉴얼에서 임의의 합격 기준이나 측정 단위를 합성하지 않는다. 순서는 DTO에 전용 필드가 없으므로 번호를 label에 포함하고 실제 화면 순서를 검증해야 한다.

4. 필요하면 반환된 task ID에 `PATCH /tasks/{taskId}`로 `{"notes":"관측 증상 및 매뉴얼 근거"}`를 기록한다. value는 실제 점검 결과가 생긴 뒤 입력한다.

5. `GET /tasks/work-order/456`으로 저장된 항목을 검증한다. 후속 단계가 실패하면 이미 생성된 작업지시서 ID를 유지하고 부분 실패로 기록한다.

## API 동작상 주의점

### Tasks는 전체 목록 동기화

로컬 `TaskController.updateEntityTasks`는 label/type/user/asset/meter/options로 기존 항목을 매칭해 보존하고, 요청에 없는 기존 항목은 삭제한다. append API로 취급하면 안 된다. 기존 작업지시서를 갱신할 때 먼저 읽고 유지할 항목까지 포함한다. `options: []`를 명시해 null 목록 문제를 피한다. label 변경은 기존 항목 삭제 및 신규 생성으로 이어질 수 있어 작업자의 결과·메모 보존을 고려해야 한다.

### Parts 연결은 실제 재고 소비

로컬 `PartQuantityController`의 `PATCH /part-quantities/work-order/{id}`는 Atlas part ID 배열을 받아 신규 부품을 수량 1로 연결하면서 `PartService.consumePart`를 호출한다. 이 서비스는 비재고품을 제외하고 재고를 차감하며 부족하면 거부한다. `PATCH /part-quantities/{partQuantityId}`로 수량을 수정하는 경로도 있다.

따라서 매뉴얼에 등장한 부품이나 조달 후보를 자동으로 Parts에 넣지 않는다. 점검 대상으로 언급한 부품과 실제 사용할 부품을 구분하고, 실제 사용 결정과 Atlas part ID 매핑을 확보한 뒤 연결한다. 현재 `SYN-C*-*`는 Copilot의 합성 마스터로 Atlas 숫자 part ID가 아니다.

### Checklist는 재사용 템플릿

`POST /checklists`는 name/description/taskBases/category/companySettings를 받는 재사용 템플릿 생성 API다. 작업지시서마다 절차를 생성하는 것과 다르며, 로컬 controller에서 CHECKLIST 구독 기능과 권한도 검사한다. 우선 직접 Tasks 등록을 권장한다.

## 수정이 필요한 연동 경계

1. Copilot: 기존 component_evidence/component_manuals/component_actions/parts_status를 구조화 payload로 유지하고, 화면·Slack용 문자열은 그 payload에서 따로 렌더링한다.
2. MCP: Tasks 생성·조회·메모 갱신 기능 또는 작업지시서 생성 후 절차를 등록하는 통합 도구를 추가한다. 현재 create-work-order에 tasks를 추가해 보내기만 해서는 전달되지 않는다.
3. Copilot: 생성 결과 numeric workOrderId와 표시 ID를 저장하고 부분 성공·재시도 상태를 관리한다. 재시도 때문에 새 작업지시서를 중복 생성하지 않도록 한다.
4. 자산 매핑: 현재 `{84: 1}`만 있다. 사진의 #90은 현재 코드 기준 asset 연결이 없다. 전체 설비의 Atlas 자산 매핑을 확보한다.
5. 부품 매핑·소비: 별도 단계로 처리하며, 합성 재고를 실제 Atlas 재고로 간주하지 않는다.

원인 해결의 최소 범위는 **짧은 description + 조치사항 Tasks 등록 + 생성 ID 보존**이다. 기존 사진의 작업지시서나 운영 재고는 이번 조사에서 변경하지 않았다.

## 2026-10-07 구현 및 배포 결과

조사 후 사용자 요청으로 다음 변경을 구현하고 실행 컨테이너에 반영했다.

- Copilot은 부품별 steps 배열과 `cmms_payload`를 유지한다. 제목·설명은 짧은 요약이며 절차는 SUBTASK 배열, 증상·출처·합성 재고·단종·대체품 정보는 항목 notes로 전달한다. 기존 승인 체크포인트는 저장된 승인 조치사항을 사용한다.
- Atlas MCP에 `add-work-order-tasks`, `get-work-order-tasks`를 추가했다. 기존 항목의 options/user/asset/meter를 보존하는 전체 목록으로 PATCH하고, 동일 label의 항목은 반복 생성하지 않는다.
- 실행 Atlas의 메모 PATCH는 생략한 value를 null로 바꿀 수 있다. 따라서 메모를 갱신하기 직전에 현재 항목을 다시 읽어 현재 value를 함께 전달한다. 사용자 메모에는 근거를 덧붙이고 같은 근거는 반복 추가하지 않는다.
- `cmms_deliveries` SQLite 테이블에 원문 payload, 숫자 ID, 표시 ID, 전송 진행 상태를 저장한다. Tasks 실패는 `partial_tasks`, 생성 응답 유실은 `creation_unknown`으로 구분한다. 생성 결과 미확인 시 자동 재생성하지 않으며 MCP의 work order POST 자동 transient retry도 껐다.
- 승인 완료된 긴급 건의 Tasks만 다시 등록하는 `POST /agent/cmms/retry`와 화면 재시도 버튼을 추가했다. 요청은 `{"thread_id": "원래 승인 스레드"}`다. 승인 흐름·Slack 발송을 다시 실행하지 않는다.
- `CMMS_ASSET_MAP` 환경변수로 확인된 자산 매핑을 설정할 수 있다. 형식은 `{"84": 1, "90": 실제_Atlas_숫자_ID}`. 빈 값은 기존 `{84: 1}`을 유지하고, `{}`는 매핑 없이 실행한다. 이번 작업에서 알 수 없는 자산 ID를 임의 배정하지 않았다.
- Parts 소비, Labors 비용, 파일 업로드, 기존 작업지시서의 일괄 재분류는 이번 구현에 포함하지 않았다. 전체 승인 원문은 Copilot의 전송 기록과 체크포인트에 보존한다.

검증: backend 304개 테스트 통과(CI 빠른 경로, 임베딩 모델 테스트 제외), MCP 19개 단위 테스트 통과(환경변수 기반 기존 통합 테스트 8개는 제외), ruff/mypy/TypeScript 검사 통과.

실제 배포 경로에서 검증용 작업지시서 `WO000011`(내부 ID 53)을 생성해 comp1·comp2의 8개 Tasks와 notes 저장, 같은 전송의 ID 재사용, MCP 반복 호출 시 항목 수 유지, 현장 추가 항목·메모·COMPLETE 값 보존을 확인했다. 해당 검증용 작업지시서는 검증 후 삭제했다. 운영 작업지시서와 부품 재고는 변경하지 않았다.
