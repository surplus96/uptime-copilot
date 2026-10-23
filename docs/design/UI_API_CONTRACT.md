# React 운영 UI 조회 계약

2026-10-19. 아래 API는 읽기 전용이며 LLM 호출·승인 재개·Slack/CMMS 전송을 하지 않는다.
기본 Docker Compose 백엔드 이미지를 `--build`로 다시 만들어야 적용된다.

## 엔드포인트

| 경로 | 주요 입력 | 반환 의미 |
|---|---|---|
| `GET /machines` | search, model, offset ≥ 0, limit 1~100 | CSV 설비 마스터와 원본·시뮬레이션 최신 관측. 필터 결과 total, models, dataset_now, queried_at |
| `GET /machines/{id}/telemetry` | id 1~100, hours 24/72/168/720, bucket_hours 1/3/6/12/24 | 구간 평균과 최소·최대·표본 수, 조회 범위와 표본 품질 정보 |
| `GET /agent/approvals` | offset ≥ 0, limit 1~100 | 최신 체크포인트에 interrupt가 남아 있는 스레드. thread_id, machine_id, priority, updated_at과 total |
| `GET /events` | 기존 limit 유지, offset/severity/search 추가 | 필터 결과 total 및 페이지 events. summary는 필터·페이지와 무관한 전체 이벤트 집계 |

`/events` 검색은 설비 번호·진단의 리터럴 부분 문자열이며 SQL 와일드카드나 쿼리로
해석하지 않는다. 긴급 → 주의 → 그 외, 최신 스캔 → 설비 번호 순으로 정렬한다.
기존 Streamlit이 사용하는 events/total 필드는 유지한다.

## 센서 시계열

- DB 조회만 수행한다. 실제 설비 피드 수집이나 위험도 추론을 추가하지 않는다.
- `dataset_now`: 원본+시뮬레이션 전체의 최신 telemetry 시각. 활성 runtime 연도 범위 적용.
- `queried_at`: 한국시간 실제 조회 시각. 위 시각들과 함께 timezone=Asia/Seoul 반환.
- 조회 구간은 `[dataset_now - (hours - 1)시간, dataset_now]`이다. 시간당 1행 데이터에서
  정확히 hours개 시각을 포함한다. 설비별 최신 시각으로 창을 옮겨 지연을 숨기지 않는다.
- 동일 시각은 하나로 합치고, 원본과 시뮬레이션이 겹치면 시뮬레이션을 우선한다.
  동일 테이블 내 중복은 마지막 rowid를 우선한다. 제외한 건수는 duplicate_samples다.
- 구간 평균은 유효한 숫자로 계산한다. NaN/Infinity는 null로 처리한다.
- 비어 있는 구간은 sample_count=0, 값=null로 반환한다. 차트는 null을 연결하지 않는다.
- 집계 일부 시간만 관측된 경우 존재하는 관측만 평균낸다. sample_count와 최소·최대를
  함께 반환하고, 툴팁·데이터 표에서 확인할 수 있다. 평균을 원시 신호로 표현하지 않는다.
- unique_samples/expected_samples는 시간당 1행 계약의 비교다. 표본이 있다고 모든 센서의
  값이 유효하다는 뜻은 아니다. 단위는 원본에 명시되지 않아 추정하지 않는다.

freshness.status는 missing(데이터 없음), lagging(데이터셋 기준 2시간 초과 지연),
aligned(데이터셋 기준 2시간 이내)다. **정상/비정상 분류가 아니다.**
wall_clock_lag_hours는 실제 조회 시각과의 차이로, 음수면 시뮬레이션 관측이 미래임을
뜻한다. 데이터셋 자체가 멈춘 경우 aligned라도 실제 시간이 최신임을 보장하지 않는다.

## 승인 대기와 작업지시서

승인 대기함은 SQLite root namespace의 스레드 ID를 열거하고 각 스레드의 최신 상태만
확인한다. 과거 interrupt 이력 때문에 이미 처리한 스레드를 다시 대기로 표시하지 않는다.
본문·사용자 질문은 목록에 포함하지 않고, 상세는 기존 상태 조회와 출력 검증을 거친다.
전체 수는 조회 시점에 확인한 대기 목록의 수이며 동시 승인과 엄밀히 동일한 스냅샷을
보장하지 않는다. 조회 비용은 저장된 스레드 수에 비례한다. 규모가 커지면 별도 인덱스가 필요하다.

진단 시작/승인 결과/상태 복구에 `work_order_data`를 부가적으로 반환한다. version=1이며
설비·긴급도·우선순위·정지 권고·관점 근거·부품별 관측/매뉴얼/조치/조달 정보를 담는다.
기존 `work_order` 문자열은 바꾸지 않는다. 구조화 사실이 없는 옛 체크포인트는 null을
반환해 원문 표시로 대체한다. 구조화 문자열도 기존 개인정보·금지어 검증을 통과해야 한다.
부품 대체품을 포함한 전체 조달 설명은 원문에서 확인할 수 있게 유지한다.

## 이번 범위 밖

인증·사용자별 승인 권한과 서버의 동시 승인 방어는 이번 읽기 UI 확장의 완료 항목이 아니다.
다중 사용자 배포 전에는 인증 주체를 스레드/승인에 연결하고 서버 측 원자적 결정·멱등성
계약을 추가해야 한다. 현재 기본 로컬 바인딩과 승인 POST 동작은 유지한다.
