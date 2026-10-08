# Tasks See details 표시 개선 (2026-10-08)

## 확인한 원인

Atlas `SingleTask.tsx`의 See details 버튼은 `task.notes` 입력 영역을 펼친다.
MCP는 이미 notes를 PATCH하지만 Atlas PostgreSQL의 `task.notes`는 varchar(255)다.
WO000012(ID 54)의 실제 승인 내용은 항목당 264~405자로, `/tasks/10` PATCH에서
`value too long for type character varying(255)`가 발생했다. 여덟 Tasks의 제목은
저장됐지만 메모는 비어 있었고 Copilot 전송 기록은 `partial_tasks`였다.

## 적용 순서

1. `scripts/atlas-task-notes.sql`로 notes를 길이 제한 없는 VARCHAR로 확장한다.
   기존 JDBC 타입을 유지해 배포된 Atlas 이미지의 Hibernate `ddl-auto: validate`와
   호환시킨다. DB 볼륨에 저장되는 변경이며 새 DB에는 별도로 적용해야 한다.
2. Copilot은 각 항목 notes에 해당 조치, 해당 부품의 번호별 전체 절차, 관측 근거,
   매뉴얼 근거·출처, 합성 부품 정보, 판정 근거를 개행과 제목으로 구분한다.
   기존 승인 상태의 사실을 사용하며 새로운 측정값이나 작업 지침을 생성하지 않는다.
3. 재시도 시 저장된 승인 항목 목록과 재구성한 목록이 일치할 때만 상세 표현을
   갱신한다. 생성된 작업지시서 ID를 재사용하고 승인·알림을 재실행하지 않는다.
4. 기존 `partial_tasks` WO000012를 같은 재시도 경로로 복구한다.
5. Atlas Sanitizer가 notes 양끝 공백·개행을 제거하므로 MCP 입력과 저장 검증도
   `trim()` 기준으로 맞춘다. 줄 내부 개행과 상세 내용은 유지한다.

## 완료 조건

- 255자를 넘는 전체 상세 설명을 저장하고 GET으로 정확히 확인한다.
- 부품별 절차와 근거가 섞이지 않고 각 항목의 조치를 식별할 수 있다.
- 재전송으로 Tasks·메모가 중복되지 않으며 현장 메모와 완료 상태가 보존된다.
- Atlas API 재시작 후에도 컬럼 확장과 메모가 유지된다.
- Copilot의 전송 기록과 체크포인트에 복구된 상태 및 전달 내용을 남긴다.

Atlas 기본 화면은 개행을 지원하는 Notes 입력창으로 상세 내용을 표시한다.
긴 내용은 해당 입력창 안에서 스크롤해서 조회한다.

## 적용 및 검증 결과

- 실행 중인 Atlas DB에 마이그레이션 적용: notes는 길이 제한 없는 VARCHAR.
- Copilot backend 및 atlas-mcp 재빌드·배포 완료.
- WO000012(ID 54)의 8개 메모 복구: 항목당 412~558자,
  저장 내용이 Copilot payload와 정확히 일치하며 전송 상태는 `sent`.
- 검증용 작업지시서에서 3,419자 다중 행 메모 저장·조회 성공.
  현장 작업자 메모, `COMPLETE` 값, 항목 추가 및 반복 전송 시 중복 방지 확인.
  검증용 작업지시서는 삭제.
- Atlas API 재시작 정상 완료 및 기존 긴 메모 유지 확인.
- Copilot CI 기본 경로 305개 통과, ruff/mypy 통과.
  MCP 20개 통과, 기존 환경 의존 테스트 8개 skipped, TypeScript 검사 통과.
- 화면 구조는 로컬 Atlas 소스에서 확인했고 저장·조회는 실제 API로 검증했다.
  브라우저에서 버튼을 직접 클릭하는 화면 자동화 검증은 실행하지 않았다.
