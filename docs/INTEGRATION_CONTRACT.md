# 설비 데이터 계약

`backend/data/pdm_dataloader.py`는 저장소 루트의 `archive/`에서 다음 CSV를 읽는다. 설비 번호는 1~100이며, 시계열은 설비 번호와 시각으로 연결한다.

| 파일 | 열 | 의미 |
| --- | --- | --- |
| PdM_machines.csv | machineID, model, age | 설비 마스터, 모델과 사용연수 |
| PdM_telemetry.csv | datetime, machineID, volt, rotate, pressure, vibration | 시간당 센서 관측값 |
| PdM_errors.csv | datetime, machineID, errorID | 오류 발생 이력. 오류 코드는 error1~5 |
| PdM_failures.csv | datetime, machineID, failure | 고장이 기록된 부품 comp1~4 |
| PdM_maint.csv | datetime, machineID, comp | 교체된 부품과 작업 시각 |

정비 이력에서 마지막 교체 이후 경과시간을 계산한다. 수요 계산에서는 시각·설비·부품이 모두 일치하는 고장과 교체를 연결한다. 같은 시각에 여러 부품이 교체될 수 있다.

운영 조회의 기준 시각은 원본과 시뮬레이션 관측 중 가장 최근 시각이다. 응답에는 실제 조회 시각도 구분해 제공한다. 센서 단위는 데이터셋에 명시되지 않았다. 차트의 결측 구간과 설비별 관측 지연 처리 방식은 [조회 계약](design/UI_API_CONTRACT.md)에 기술했다.
