# 세션 06 — 텔레그램 승인 워크플로 구현 보고서

## 세션 목표

문서 06의 Telegram 환경 사전 검사, 담당자 보고, 현재 보고 메시지 직접 답장 조회, 등록 담당자 검증, 보수적 자연어 분류, 승인·수정·보류 상태 기록과 사용자 명령을 실제 로컬 도구로 구현한다. 명확한 승인 전에는 ImageGen을 호출하지 않는다.

## 시작 전 이월 점검

- 세션 05 보고서의 `미해결 누락`: 없음
- 기존 전체 회귀 테스트 22개: 재실행 통과
- 아키텍처 자체 검증: 재실행 통과
- 선행 보완 작업: 없음

## 구현 범위

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_APPROVER_IDS` 형식 검사와 비밀정보 마스킹
- 메시지를 보내지 않는 `getMe`·`getChat`·`getChatMember` 사전 검사
- 요약 메시지와 전체 `report.md` 파일의 순차 전송
- 요약·보고서 파일별 전송 체크포인트와 부분 실패 복구
- 같은 성공 명령의 무네트워크 멱등 처리
- 명시적 재전송 시 같은 리비전 유지, 새 보고 메시지 활성화, 이전 승인 무효화
- 대상 그룹, 현재 요약 메시지 직접 답장, 등록 담당자, 텍스트 존재 여부 검사
- `update_id` 커서와 답장 이벤트를 이용한 중복 처리 방지
- `approved`, `revision_requested`, `hold`, `unclear` 분류와 안전 우선순위 집계
- 보고 후 24시간이 지난 답장의 무네트워크 거부와 재전송 안내
- 승인된 보고 메시지 ID와 승인 당시 콘텐츠 서명 저장
- 승인 후 콘텐츠 변경 감지와 `NEEDS_REVIEW` 전환 기반
- `승인 확인`, `보고 재전송`, `작업 상태` 사용자 명령 라우팅
- 승인 대기 작업이 정확히 하나일 때만 작업 ID 생략 허용
- 생성된 이미지의 번호순 전송, 부분 성공 보존, 전달 멱등 처리 기반
- API 네트워크 오류·429·5xx의 즉시 한 번 재시도

구현은 [Telegram Bot API](https://core.telegram.org/bots/api)와 [Telegram 봇 FAQ](https://core.telegram.org/bots/faq)의 메서드·업데이트 조회 계약을 기준으로 했으며, v1의 폴링 방식과 서버 없는 구조를 유지했다.

## 변경 파일

- `plugins/devotional-shorts-prep/.codex-plugin/plugin.json`
- `plugins/devotional-shorts-prep/README.md`
- `plugins/devotional-shorts-prep/scripts/telegram_bot.py`
- `plugins/devotional-shorts-prep/scripts/approval_workflow.py`
- `plugins/devotional-shorts-prep/scripts/job_store.py`
- `plugins/devotional-shorts-prep/scripts/test_telegram_workflow.py`
- `plugins/devotional-shorts-prep/scripts/validate_architecture.py`
- `plugins/devotional-shorts-prep/skills/devotional-shorts/SKILL.md`
- `plugins/devotional-shorts-prep/skills/devotional-shorts/references/system-boundaries.md`
- `plugins/devotional-shorts-prep/skills/devotional-shorts/references/telegram-contract.md`
- `reports/README.md`
- `reports/requirements-traceability.md`
- `reports/session-06-telegram-approval-workflow.md`

플러그인 버전은 `0.5.0`, 콘텐츠 방법론 버전은 `1.0.0`을 유지한다. Telegram 연결 추가는 콘텐츠 방법론 변경이 아니므로 방법론 버전과 해시는 변경하지 않았다.

## 공개 명령

### Telegram 전송 도구

| 명령 | 구현 결과 |
| --- | --- |
| `preflight` | 환경·토큰·그룹·권한을 검사하되 테스트 메시지는 보내지 않음 |
| `send-report` | 검증된 `DRAFT`의 요약과 전체 보고서를 보내고 현재 승인 기준 메시지 저장 |
| `check-replies` | 현재 보고에 대한 답장을 조회해 유효·무효 원문과 근거 메타데이터 기록 |
| `send-images` | `GENERATING` 상태의 생성 완료 이미지만 순차 전달하고 성공 장면 보존 |

### 승인 통합 도구

| 명령 | 사용자 요청 | 구현 결과 |
| --- | --- | --- |
| `check-approval` | `승인 확인 [작업 ID]` | 답장 조회·분류·우선순위 집계·상태 기록 |
| `resend-report` | `보고 재전송 [작업 ID]` | 새 요약 메시지를 현재 승인 기준으로 설정 |
| `status` | `작업 상태 [작업 ID]` | Telegram 설정과 네트워크 없이 한국어 상태 요약 |
| `classify` | 내부 진단 | 답장 한 건을 보수적 규칙으로 분류 |

저수준 Telegram 도구는 승인 의미를 결정하지 않고, 승인 통합 도구가 검증된 원문만 분류한다.

## 문서 06 요구사항 점검

| 항목 | 결과 | 증거 |
| --- | --- | --- |
| 필수 환경변수와 비밀정보 분리 | 통과 | 누락·형식 오류의 무네트워크 테스트, 빈 `.env.example`, 토큰 마스킹 테스트 |
| 메시지 없는 사전 검사 | 통과 | 호출 순서 `getMe`·`getChat`·`getChatMember`와 전송 0회 검사 |
| 요약 후 보고서 파일 전송 | 통과 | 호출 순서·요약 필수 필드·파일 답장 연결 검사 |
| 보고 부분 실패 복구 | 통과 | 요약 ID 보존 후 파일만 재시도하는 회귀 테스트 |
| 전송 직후 중단의 중복 방지 | 통과 | 두 체크포인트가 있으면 전송 API 0회인 회귀 테스트 |
| 보고 재전송과 과거 승인 무효화 | 통과 | 리비전 유지, 새 메시지 ID, 답장 목록 초기화, 커서 유지 검사 |
| 현재 메시지 직접 답장만 인정 | 통과 | 이전 메시지·다른 메시지 답장 무효 사유 검사 |
| 등록 담당자만 승인 | 통과 | 미등록 사용자 답장 무효 이벤트 검사 |
| 승인·수정·보류·불명확 분류 | 통과 | 대표 문구와 충돌 문구 분류 테스트 |
| 안전 우선순위 집계 | 통과 | 수정 요청 우선, 다음 보류, 다음 승인 검사 |
| 24시간 승인 창 | 통과 | 24시간 1초 초과 시 네트워크 0회와 상태 유지 검사 |
| 작업 ID 생략의 단일 후보 규칙 | 통과 | 후보 0·1·2개 동작 검사 |
| 승인 중복 처리 방지 | 통과 | 커서·멱등 이벤트와 승인 후 재조회 무네트워크 검사 |
| 승인 콘텐츠 불변성 | 통과 | 승인 서명 저장 후 프롬프트 변조·해시 재계산도 감지 |
| 로컬 손상 시 외부 조회 차단 | 통과 | 무결성 오류에서 네트워크 0회와 `NEEDS_REVIEW` 검사 |
| 손상 작업 상태 조회 | 통과 | 보존된 `validation-error.json`으로 한국어 복구 상태 표시 |
| 이미지 부분 전달 기반 | 통과 | 성공 장면 보존, 다음 실행에서 실패 장면만 전송 검사 |
| 승인 외 결과의 ImageGen 금지 | 통과 | 승인 확인 도구가 상태만 기록하고 ImageGen 경로를 호출하지 않음 |

## 구현 중 발견하여 세션 안에서 수정한 항목

1. 요약 전송 뒤 파일 업로드가 실패하거나 두 전송 완료 직후 프로세스가 중단될 수 있었다. 요약과 문서 전송을 각각 이벤트 체크포인트로 기록하고, 재실행 시 완료된 단계는 다시 보내지 않도록 보강했다.
2. 보고 재전송 뒤 이전 답장과 승인이 남을 수 있었다. 새 요약 메시지 ID를 활성화할 때 승인 결과와 허용 답장 목록을 초기화하고 조회 커서만 보존하도록 수정했다.
3. 승인 이후 프롬프트를 바꾸고 프롬프트 해시까지 다시 계산하면 단일 필드 해시만으로는 변조를 놓칠 수 있었다. 묵상 포인트·원고·장면의 승인 콘텐츠 서명을 승인 이벤트에 저장하고 재검증하도록 보강했다.
4. 손상된 작업에서 승인 답장부터 조회할 가능성이 있었다. 로컬 무결성 검사를 네트워크보다 먼저 실행하고 실패 시 `NEEDS_REVIEW`로 전환하도록 수정했다.
5. 손상된 `job.json`은 일반 작업 로더로 상태를 표시할 수 없었다. 명시적 작업 ID의 상태 조회는 복구 마커를 직접 사용할 수 있도록 라우팅을 보완했다.
6. Telegram 응답의 불리언 값이 Python에서 정수로 취급될 수 있었다. 메시지·사용자·시각 ID의 불리언과 0 이하 값을 신뢰 경계에서 거부하도록 강화했다.

모든 항목은 세션 종료 전에 회귀 테스트로 고정했다.

## 독립 사용 시험

새 에이전트가 세션 05에서 생성한 플러그인 외부의 실제 로컬 작업을 사용자 명령 관점에서 검사했다. 구현의 기대 상태는 알려주지 않았고 실제 Telegram과 ImageGen 호출을 금지했다.

- 프로젝트: `/private/tmp/devotional-shorts-review.zvILXY`
- 작업: `ds-20260812-141904-c47b4d83`, `r001`
- 공식 `status`: `DRAFT`, 보고 시각·메시지 ID·승인 결정 없음
- 공식 `validate`: 통과
- 장면: `PENDING` 19개, `FAILED` 0개
- 게이트 판단: 담당자 보고 미완료, 승인 확인 불가, 이미지 생성 불가
- 안내한 다음 조치: 설정된 환경에서 `preflight` 후 현재 `DRAFT`에 `send-report`
- 외부 호출: Telegram 0회, ImageGen 0회

독립 시험은 보고되지 않은 로컬 초안을 승인되었다고 오판하지 않았고, 정확한 다음 단계만 안내했다.

## 최종 검증 명령 및 결과

- 표준 `unittest` 전체 회귀 테스트: 39개 통과
- Python 바이트코드 컴파일: 통과
- 아키텍처 자체 검증: 통과 (`plugin_version=0.5.0`, `method_version=1.0.0`)
- 공식 스킬 빠른 검증: 통과 (`Skill is valid!`)
- 공식 플러그인 검증: 통과
- `telegram_bot.py --help`: `preflight`, `send-report`, `check-replies`, `send-images` 노출 확인
- `approval_workflow.py --help`: `classify`, `check-approval`, `resend-report`, `status` 노출 확인
- `git diff --check`: 통과
- 실행 가능한 문서·소스의 미완성 플레이스홀더: 없음
- 끝 공백, 플러그인 내부 사용자 절대경로: 없음
- `.env.example`: 변수명 세 개만 있고 값은 모두 비어 있음
- 플러그인 내부 이미지와 프로젝트 루트 `jobs/`: 없음

실제 Bot API 종단간 시험은 실제 토큰·그룹·담당자 ID가 제공되지 않아 실행하지 않았다. 이는 구현 누락이 아니라 외부 인수 검증 자료이며, 자격 증명이 제공되는 경우 세션 09의 신규 설치·종단간 시험에서 수행한다. 이번 세션의 모든 네트워크 행위는 모의 API로 검증했다.

## 미해결 누락

- 없음

## 다음 세션 선행 작업

세션 07 시작 전에 39개 전체 회귀 테스트, 아키텍처 검증과 세션 06의 `미해결 누락`을 다시 확인한다. 통과하면 문서 07의 승인 콘텐츠 게이트, 장면별 ImageGen 호출, 파일 검증·원자적 저장, 장면별 한 번 재시도, `NEEDS_REVIEW`와 Telegram 이미지 전달 완료 상태를 구현한다.

## 세션 결론

문서 06의 현재 완료 기준을 모두 충족했다. 검증된 로컬 보고서만 전송되고, 현재 보고 메시지의 등록 담당자 직접 답장만 승인 근거가 되며, 수정·보류·불명확 결과는 이미지 단계로 넘어가지 않는다. 전송·조회·승인 기록은 멱등하고 실패 시 로컬 결과를 보존한다. 세션 07로 이월할 구현 누락은 없다.
