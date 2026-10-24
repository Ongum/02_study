# Lost Ark Game Analytics Platform 작업 보고서

작성 기준 시각: 2026-10-22 07:47 (Asia/Tokyo)  
대상: Steam App ID `1599340`  
상태: 로컬 수집·품질 검증·분석 프로토타입 완료, 클라우드 미배포

## 1. 이번에 실제 완료한 작업

1. 기존 구조, 정책, 리뷰 파서, JSONL 중복 제거, HTTP 재시도,
   Retry-After, 체크포인트 및 복구 테스트를 재점검했다.
2. 고정 날짜 범위를 중단 후 재개할 수 있는 리뷰 backfill 작업을
   구현했다. Raw는 내용 해시 기반 이름으로 한 번만 만들고, 정규화
   데이터 fsync 이후에만 다음 커서를 체크포인트에 기록한다.
3. 최초 정책 구간인 최근 90일을 끝까지 수집하고 빈 종료 페이지를
   확인했다.
4. 표본 정밀도 평가 후, 겹치지 않는 직전 90일만 추가 수집하여 총
   180일 데이터셋을 만들었다. 이미 수집한 최근 90일은 재요청하지
   않았다.
5. 리뷰 이력과 최신 상태, 일별·주별·언어별 집계, 영어 부정 리뷰의
   키워드 기반 불만 탐색, 기간 비교, 품질 지표를 생성했다.
6. 공식 Steam 현재 접속자 endpoint의 수집기와 idempotent run ID 동작을
   구현하고 실제 스냅샷 1건을 저장했다.
7. 공식 Steam News endpoint의 제한된 180일 수집기와 체크포인트를
   구현하고, 뉴스 게시물에서 업데이트 후보를 생성했다.
8. 뉴스 게시 시각을 명시적인 미검증 proxy로 사용하는 14일 전후
   update-impact 분석 테이블 초안을 만들었다.
9. BigQuery/dbt 이동 시 필요한 grain, 파티션, 클러스터, 테스트 설계를
   문서화했다. 실제 GCP 작업은 하지 않았다.
10. 180일 최대 수정 시각에서 실험적 24시간 overlap의 Updated
    체크포인트를 만들고, 최초 수집·즉시 replay·약 9시간 후 시간차
    수집을 실행했다. 시간차 수집에서 새 행 3건을 저장했다.
11. 현재 접속자 스냅샷을 약 9.15시간 간격으로 한 번 더 수집하고,
    최신 뉴스 구간을 bounded 증분 확인했다.

공식 API 계약은 Steam의
[리뷰 API](https://partner.steamgames.com/doc/webapi/IUserReviewsService),
[현재 접속자 API](https://partner.steamgames.com/doc/webapi/ISteamUserStats),
[뉴스 API](https://partner.steamgames.com/doc/webapi/ISteamNews)를 기준으로
검토했다.

## 2. 수정·생성한 파일

### 수집·커넥터

- `src/entertainment_platform/jobs/steam_reviews_backfill.py`
- `src/entertainment_platform/connectors/steam/players.py`
- `src/entertainment_platform/jobs/steam_current_players.py`
- `src/entertainment_platform/connectors/steam/news.py`
- `src/entertainment_platform/jobs/steam_news_backfill.py`

### 분석·마트

- `src/entertainment_platform/analytics/__init__.py`
- `src/entertainment_platform/analytics/review_analysis.py`
- `src/entertainment_platform/analytics/merge_review_history.py`
- `src/entertainment_platform/analytics/update_impact.py`
- `src/entertainment_platform/analytics/version_coverage.py`
- `src/entertainment_platform/analytics/player_analysis.py`
- `src/entertainment_platform/analytics/risk_evaluation.py`
- `src/entertainment_platform/analytics/update_classification.py`
- `src/entertainment_platform/analytics/update_decision_mart.py`
- `src/entertainment_platform/analytics/event_time_evidence.py`
- `src/entertainment_platform/analytics/collection_health.py`
- `src/entertainment_platform/jobs/build_local_analytics.py`
- `src/entertainment_platform/jobs/bootstrap_updated_checkpoint.py`

### 테스트

- `tests/test_steam_backfill.py`
- `tests/test_review_analysis.py`
- `tests/test_players_and_merge.py`
- `tests/test_steam_news.py`
- `tests/test_update_impact.py`
- `tests/test_updated_bootstrap.py`
- `tests/test_player_analysis.py`
- `tests/test_risk_evaluation.py`
- `tests/test_update_classification.py`
- `tests/test_update_decision_mart.py`
- `tests/test_event_time_evidence.py`
- `tests/test_collection_health.py`
- `tests/test_build_local_analytics.py`

### 문서

- `README.md`
- `docs/DECISIONS.md`
- `docs/ROADMAP.md`
- `docs/PROGRESS.md`
- `docs/COST_GUARDRAILS.md`
- `docs/contracts/steam_review_normalized.md`
- `docs/contracts/steam_review_backfill.md`
- `docs/contracts/steam_updated_incremental.md`
- `docs/contracts/steam_players_news.md`
- `docs/contracts/bigquery_dbt_migration.md`
- `dbt/README.md`
- `docs/progress_report.md`

### 생성된 로컬 데이터(모두 `.gitignore` 대상)

- `output/silver_review_history_90d.jsonl`: 450행
- `output/silver_review_history_prior90d.jsonl`: 431행
- `output/silver_review_history_180d.jsonl`: 881행
- `output/silver_reviews_current_180d.jsonl`: 881행
- `output/mart_review_daily_180d.jsonl`: 179행
- `output/mart_review_weekly_180d.jsonl`: 27행
- `output/mart_review_language_180d.jsonl`: 21행
- `output/mart_issue_language_180d.jsonl`: 7행
- `output/mart_update_impact_draft_180d.jsonl`: 8행
- `output/silver_app_news_180d.jsonl`: 10행
- `output/fact_player_snapshot.jsonl`: 2행
- `output/player_snapshot_summary.json`: 2개 관측의 변화와 표본 부족 표시
- `output/dim_update_event_classification.jsonl`: 뉴스 기반 이벤트 분류 10행
- `output/mart_update_decision_180d.jsonl`: 이벤트 의사결정 마트 10행
- `output/dim_event_time_evidence.jsonl`: 공지 시각 증거 10행(UTC 파싱 4행)
- `output/mart_collection_health.jsonl`: 수집 현황·품질 4행
- `output/collection_health_summary.json`: 고정 기준 시각 현황 요약
- `output/local_analytics_run_manifest.json`: 8단계 재생성 실행 증거
- `output/silver_review_history_updates.jsonl`: 103행
- `output/updated_version_coverage_summary.json`: Updated/baseline 비교
- 각 수집 체크포인트 및 aggregate summary JSON
- `raw_data/` 아래 내용 해시 기반 Raw 응답과 리뷰 metadata sidecar

Raw 리뷰 응답에는 공개 Steam 작성자 식별자가 포함될 수 있으므로 로컬
제한 데이터로 유지하며, 정규화 리뷰에는 작성자 Steam ID를 저장하지
않았다.

## 3. 실행한 테스트와 결과

최종 결과:

```text
python -m pytest -q                 50 passed in 0.54s
python -m ruff check src tests      passed
python -m ruff format --check ...   passed
python -m compileall -q src         passed
```

검증한 주요 실패 경로:

- backfill 1페이지 후 종료 → 저장된 커서부터 재개
- 완료된 backfill 재실행 → HTTP 0회, 추가 쓰기 0행
- 중복 리뷰 버전 병합 → 중복 추가 0행
- 리뷰 페이지 실패/상한/부분 쓰기 → 잘못된 체크포인트 확정 방지
- 현재 접속자 동일 run ID → HTTP 0회
- 뉴스 범위 하한 통과 → 완료 체크포인트 후 재요청 0회
- Updated bootstrap 최대 수정 시각 선택 및 동일 설정 재실행 안전성
- 실제 Updated 즉시 replay → 100건 재관측, 추가 쓰기 0행
- 429, 5xx, 통신 오류, Retry-After, 최대 재시도

실제 발생한 실패와 처리:

- 이벤트 시각 파서의 첫 실행에서 Windows 환경에 IANA `tzdata`가 없어
  PT 변환 테스트 2건과 실제 작업이 실패했다. 외부 의존성을 추가하지
  않고 미국 태평양 2007년 이후 DST 규칙을 명시적으로 구현하고 변환
  근거 필드를 추가한 뒤 전체 회귀 검사를 통과했다.
- 2026-10-22 첫 병렬 명령에서 세 작업의 CLI 옵션 이름을 잘못 사용해
  `argparse`가 실행 전 종료했다. 네트워크 요청과 데이터 변경은 없었다.
  각 모듈의 실제 도움말에 맞춰 다시 실행해 성공했다.
- 첫 version-coverage 재계산에서 존재하지 않는 논리 파일명을 기준으로
  지정해 0행 요약이 생성됐다. 실제 881행 history 파일로 즉시 다시
  계산하고 두 summary 파일을 올바른 103행 결과로 덮어썼다.
- 새 player 분석 모듈의 첫 Ruff 검사에서 Python 3.11에 불필요한 `Z`
  치환 한 건이 실패했다. 직접 수정 후 전체 테스트·lint·format·compile을
  다시 실행해 모두 통과했다.
- 제한된 실행 환경에서 첫 90일 명령이 DNS 차단으로 4번의 로컬 전송
  시도 후 실패했다. Steam 서버에 도달한 요청은 없었다. 환경 승인
  경로로 동일한 bounded 명령을 다시 실행해 성공했다.
- 뉴스 합성 테스트 한 건에서 2025년 Unix 값을 2026년 범위에 넣은
  fixture 오류가 발견됐다. 명시적 UTC datetime으로 수정한 뒤 전체
  32개 회귀 테스트를 통과했다.

전용 정적 타입 검사기는 프로젝트에 설정되어 있지 않아 실행하지
않았다. 대신 Python 3.11 타입 표기, Ruff 및 compile 검사를 수행했다.

## 4. 실제 API 수집 데이터 건수

### 리뷰

| 생성일 범위(UTC) | API `total_matching` | 저장 고유 리뷰 | 데이터 페이지 | 종료 페이지 | 실제 HTTP 시도 |
|---|---:|---:|---:|---:|---:|
| 2026-07-23 ~ 2026-10-20 | 450 | 450 | 5 | 1 | 6 |
| 2026-04-24 ~ 2026-07-22 | 431 | 431 | 5 | 1 | 6 |
| 합계 | 881 | 881 | 10 | 2 | 12 |

모든 성공 페이지는 첫 HTTP 시도에 완료됐다. 최근 90일 완료
체크포인트를 재실행했을 때 요청은 0회였다. Raw+metadata 크기는 두
범위 각각 364,667 bytes와 355,739 bytes였다.

### 뉴스와 현재 접속자

- 뉴스: HTTP 1회, 180일 범위 게시물 10건, 제목 기반 업데이트 후보
  8건. 범위보다 오래된 항목을 같은 페이지에서 확인하여 하한 도달을
  검증했다.
- 현재 접속자: 각 HTTP 1회의 실제 스냅샷 2건. 첫 관측은
  `2026-10-21T13:38:20.197810Z`에 **5,260명**, 두 번째는
  `2026-10-21T22:47:10.037952Z`에 **7,201명**이었다. 9.1472시간 동안
  **+1,941명(+36.90%)**이지만 서로 다른 UTC 시간대의 두 점뿐이므로
  업데이트 효과나 일간 추세로 해석할 수 없다.
- 최신 뉴스 증분 확인: HTTP 1회, `2026-10-21..2026-10-22` 범위 신규
  게시물 0건. 빈 결과도 완료 체크포인트로 기록했다.

### Updated 실제 replay

- bootstrap high watermark: `2026-10-20T21:48:24Z`
- 실험 overlap: 86,400초(24시간), 운영값으로 미확정
- 첫 실행: HTTP 1회, 100건 저장, overlap floor 도달, checkpoint 전진
- 즉시 두 번째 실행: HTTP 1회, 100건 관측, 0건 추가, checkpoint 불변
- 약 9시간 후 세 번째 실행: HTTP 1회, 100건 관측, **3건 추가**,
  overlap floor 도달, high watermark를 `2026-10-21T19:25:31Z`로 전진
- 세 번째 실행을 같은 run ID로 다시 실행: HTTP 1회, 100건 관측,
  **0건 추가**, checkpoint 불변(저장 멱등성 확인)
- 누적 103행 중 180일 baseline과 겹치는 review ID 60건은 모두 동일
  버전 재생
- baseline 밖 43건: 생성일이 baseline보다 오래된 리뷰 38건, baseline
  종료 후 신규 리뷰 5건
- 시간차 실행까지도 baseline 881건의 새 버전은 0건. 다만 오래된
  리뷰 1건의 최신 수정과 종료 후 신규 리뷰 2건이 새로 관측됐다.

2026-10-22 속행에서 성공적으로 Steam에 도달한 요청은 최초 병렬
수집 3회와 Updated 멱등 재실행 1회였다. 앞선 실행까지 포함한 총량은
Raw/metadata와 각 실행 로그를 기준으로 해석해야 하며, 429를 유도하거나
제한을 시험하지 않았다.

## 5. 데이터 품질·중복·누락 점검

180일 결합 결과:

| 품질 항목 | 결과 |
|---|---:|
| history version 행 | 881 |
| 고유 리뷰 / current 행 | 881 |
| `(appid, recommendationid, updated_at)` 중복 | 0 |
| 필수 필드 결측 | 0 |
| 생성일 범위 밖 행 | 0 |
| 해석 불가능한 timestamp | 0 |
| `updated_at < created_at` | 0 |
| 작성 후 수정된 것으로 보이는 리뷰 | 54 |
| 리뷰가 존재한 날짜 | 179 / 180 |

수집기는 두 범위 모두 빈 종료 페이지까지 도달했고, 페이지 전체에서
`total_matching`이 변하지 않는지 검사했다. 하루에 리뷰가 없었던 것은
수집 누락으로 채우지 않았으며 0건 날짜로 해석해야 한다.

중요한 이력 한계가 있다. 날짜 범위 backfill은 수집 시점에 보이는
최신 리뷰 상태만 반환하므로, 54건의 과거 수정 전 텍스트나 추천 상태를
복원할 수 없다. history 테이블의 grain은 향후 Updated 스트림에서 새
버전을 누적하기 위한 것이며, 현재 881행은 리뷰당 한 관측 버전이다.

## 6. 분석 가능한 KPI와 표본 한계

### 실제 측정 결과

- 전체 881건: 긍정 427, 부정 454, 부정 비율 **51.53%**.
- 전체 부정 비율 Wilson 95% 구간: **48.23% ~ 54.82%**.
- 첫 90일 부정 비율 51.04%, 다음 90일 52.00%, 변화 **+0.96%p**.
- 두 기간 변화의 근사 95% 구간: **−5.65%p ~ +7.56%p**.
  0을 포함하므로 차이를 확정할 수 없고 업데이트 효과도 아니다.
- UTC 주별 27개 행, 리뷰 수 중앙값 35, 최소 6, 최대 47.
- 일별 179개 행은 모두 30건 미만이며, 주별 27개 중 9개도 30건
  미만으로 `low_sample_flag`가 설정됐다.

상위 언어 표본:

| 언어 | 리뷰 | 부정 | 부정 비율 |
|---|---:|---:|---:|
| English | 418 | 231 | 55.26% |
| French | 99 | 72 | 72.73% |
| Spanish | 99 | 26 | 26.26% |
| Brazilian Portuguese | 84 | 26 | 30.95% |
| German | 69 | 53 | 76.81% |

언어별 차이는 구성·문화·시점·자기선택 차이가 섞인 기술통계이며 언어
집단의 특성으로 일반화할 수 없다.

영어 부정 리뷰 231건에 대한 키워드 baseline은 104건에서 하나 이상의
범주를 찾았다. 다중 라벨 상위 범주는 balance/gameplay 52,
content/progression 47, monetization/grind 43이었다. 127건은 미분류였다.
이는 사람 라벨로 평가한 NLP 분류기가 아니므로 “주요 불만 확정”이
아니라 후속 라벨링 후보 탐색 결과다.

### 업데이트 전후 표본 충분성

180일 평균은 하루 약 4.89건이다. 업데이트 전후 각각 7일이면 약
34.3건, 각각 14일이면 약 68.5건이다. 최악 조건에서 부정 비율 차이의
근사 95% 오차폭은 각각 **±23.68%p**, **±16.74%p**다. 따라서:

- 주별 큰 방향 변화와 탐색적 이상 징후는 볼 수 있다.
- 작은/중간 크기의 업데이트 효과를 안정적으로 구분하기 어렵다.
- 14일 창은 7일보다 낫지만 다른 이벤트와 계절 요인이 섞일 가능성이
  커진다.
- 180일 확장은 비교할 이벤트와 기준 기간은 늘렸지만 한 이벤트 주변
  일평균 리뷰 수는 늘리지 못했다.

365일은 현재 속도를 단순 연장하면 약 1,786건, 100건 기준 약 18개
데이터 페이지로 예상된다. 더 많은 업데이트 사례와 계절 기준선에는
도움이 되지만, 특정 최신 업데이트 전후의 표본 밀도를 높이지 않으므로
이번에는 추가 수집하지 않았다. 이 수치는 실제 365일 건수가 아니라
180일 관측률 기반 예시다.

뉴스 후보 8건으로 14일 전후 초안을 생성했다. 7건은 데이터 범위가
완전했고, 1건은 사후 창이 잘렸으며 표본도 부족했다. 모든
`effective_at`이 null이므로 게시 시각 기반 수치는 배포 시각 분석으로
사용할 수 없다. 인과 주장도 허용하지 않는다.

### 편향 구분

- 데이터 자체의 선택 편향: Steam 리뷰 작성자는 전체 이용자의 대표
  표본이 아니며, 불만/만족이 강한 이용자가 더 작성할 수 있다.
- 서비스 범위 편향: 이 데이터는 Lost Ark Steam/global 서비스이며
  다른 지역·플랫폼 전체를 대표하지 않는다.
- 수집 설계 편향: 생성일 범위로 선택했으므로 범위 안에 작성된 리뷰의
  수집 시점 최신 상태를 본다. 익명 응답은 최대 10분 캐시될 수 있다.
- 분석 방법 편향: 불만 baseline은 영어만 지원하며 단순 키워드다.
  다른 언어의 낮거나 높은 부정률과 불만 유형을 같은 방법으로 비교할
  수 없다.

## 7. 현재 미완료 작업과 이유

- 실제 Updated 반복 수집: live floor, 즉시 replay와 약 9시간 시간차
  수집은 검증했다. 3건의 신규 저장과 재실행 0건을 확인했지만 운영
  overlap을 확정할 장기 관측 횟수는 아직 부족하다.
- 과거 리뷰 버전 복원: 공식 현재 응답으로 과거 수정 전 버전을 만들
  수 없다. 앞으로 관측되는 변경만 누적 가능하다.
- 현재 접속자 변화 KPI: 스냅샷 2건의 단순 차이는 계산했지만 시간대가
  달라 비교 가능한 일/주 시계열이 없다.
- 업데이트 `effective_at`: 뉴스 게시 시각과 실제 적용 시각을 독립적으로
  검증하지 않았다.
- NLP 성능: 사람 라벨, precision/recall, 다국어 평가가 없다.
- issue detection latency: 시간 순서의 반복 수집과 라벨 기준 onset이
  없어 측정할 수 없다.
- dbt/BigQuery/GCS/Cloud Run/Scheduler: 설계만 있으며 프로젝트/IAM/IaC
  결정과 비용 승인이 없다.
- 사회적 성과: 실제 운영자가 결과를 사용하거나 이용자 경험이
  개선됐다는 증거가 없다.

## 8. 추가 승인이 필요한 작업

다음은 현재 승인 범위를 넘어가므로 실행하지 않았다.

- GCP 프로젝트, 버킷, BigQuery dataset/table, Cloud Run Job, Scheduler,
  서비스 계정, 예산 알림 생성
- 결제 계정 연결 또는 유료 API 사용
- Terraform 적용, dbt BigQuery 실행
- 외부 저장소 push, PR 또는 공개 대시보드 배포

향후 클라우드 작업 전에는 IaC 방식을 선택하고 project ID, billing
owner, IAM 이름, 보존 기간 및 raw text 접근 정책을 승인해야 한다.

## 9. 다음 우선순위 3개

1. **Updated live 반복 검증**: 현재 24시간 overlap으로 며칠간 같은
   시간대 수집을 더 누적하여 새 버전·재생 중복·floor 도달을 측정하고
   운영 overlap을 결정한다.
2. **이벤트 시각 검증과 분석 보강**: Release Notes 후보부터 실제 적용
   시각의 근거 URL을 확인하여 `effective_at`을 채우고, 겹치는 이벤트와
   불완전 창을 제외한 사전 정의 분석을 다시 실행한다.
3. **반복 스냅샷·라벨링**: 현재 접속자를 같은 시간대 비교가 가능할 만큼
   누적하고, 영어 부정 리뷰의 층화 표본을 사람 기준으로 라벨링하여
   키워드 baseline의 precision/recall과 탐지 지연을 평가한다.

## 10. 수집 → 저장 → 정규화 → 분석 흐름

쉽게 말하면 다음 순서다.

1. **수집**: Steam에 “이 날짜 사이의 리뷰 100개를 주세요”라고 요청한다.
   다음 페이지 커서를 받아 빈 페이지가 나올 때까지, 정해 둔 최대
   페이지 안에서만 이동한다.
2. **Raw/Bronze 저장**: 받은 JSON 원문을 내용 해시가 들어간 새 파일로
   보관한다. 기존 파일을 덮어쓰지 않는다. 요청 범위·응답 크기·해시·
   재시도 수를 별도 metadata에 남긴다.
3. **검증과 Silver 정규화**: 필수 필드와 타입, 시간 순서를 검사하고
   UTC 시간으로 바꾼다. 분석에 필요 없는 작성자 Steam ID는 제거한다.
   리뷰 ID와 수정 시각이 같은 행은 다시 쓰지 않는다.
4. **체크포인트**: 원본과 정규화 행이 디스크에 안전하게 기록된 뒤에만
   “다음에 시작할 커서”를 저장한다. 중간 실패 시 마지막 안전 지점부터
   재개한다.
5. **current와 history 분리**: history는 관측한 버전을 쌓고, current는
   리뷰별 가장 최근 버전 하나를 선택한다. 과거에 관측하지 않은 수정
   전 버전은 만들어내지 않는다.
6. **Gold형 분석**: current에서 날짜·주·언어별 건수와 부정률을 계산하고
   Wilson 구간과 표본 부족 표시를 붙인다. 뉴스는 먼저 후보 테이블로
   만들며 실제 적용 시각이 확인되지 않으면 proxy임을 표시한다.
7. **해석**: 숫자가 커 보이더라도 표본 수, 범위 잘림, 캐시, 리뷰
   자기선택, 이벤트 중첩을 확인한다. 단순 전후 변화는 원인 증명이
   아니며, 실제 운영 성과로 보고하지 않는다.

이 흐름은 게임 외 웹툰·웹소설에서도 공통 HTTP/재시도/Raw/체크포인트/
품질 유틸리티를 재사용하되, 도메인별 Gold 마트와 해석은 분리하는
현재 아키텍처 원칙을 유지한다.

## 로컬 재현 명령

```text
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
python -m entertainment_platform.analytics.player_analysis --input output/fact_player_snapshot.jsonl --summary output/player_snapshot_summary.json
python -m entertainment_platform.analytics.version_coverage --baseline output/silver_review_history_180d.jsonl --updated output/silver_review_history_updates.jsonl --summary output/review_version_coverage.json
```

실제 API 재수집 명령은 호출 시점의 공개 데이터를 추가 관측하므로,
고정 분석 재현에는 위 로컬 Raw/Silver 산출물을 사용한다. 미검증 가정은
24시간 overlap의 장기 운영 적합성, 서로 다른 시각 접속자 비교 가능성,
뉴스 게시 시각과 실제 패치 적용 시각의 일치 여부다.

## 2026-10-22 추가 진행: 로컬 증분 수집 통합 실행

- `collect_local_incremental` 진입점과 설정을 추가했다. 로컬 배타 잠금,
  소스별 독립 실행, 실행 매니페스트를 제공하며 스케줄러는 아니다.
- 실제 공개 API 실행 `lostark-local-20261022-04`는 세 단계 모두
  완료했다: Updated 1페이지/100관측/신규 0행, 현재 접속자 5,476명/신규
  1행, 최근 48시간 뉴스 신규 0행이다. 전체 실행은 약 0.92초였다.
- 샌드박스 네트워크가 차단된 앞선 실행은 세 소스 모두 4회 통신 실패로
  `partial_failure` 매니페스트를 남겼다. 이 실패는 삭제하거나 성공으로
  바꾸지 않았다.
- 검증: 전체 pytest 55개 통과, Ruff lint/format 및 Python compileall 통과.
- GCP 리소스 생성·배포·과금은 수행하지 않았다.
- 후속 로컬 분석 재생성 `local-rebuild-20261022-03`은 네트워크 호출 없이
  8단계를 모두 완료했다. 플레이어 스냅샷은 3개가 되었고 세 증분 소스는
  최신 상태지만, 반복 실행 이력이 부족해 분석 준비도는 여전히 `limited`다.
