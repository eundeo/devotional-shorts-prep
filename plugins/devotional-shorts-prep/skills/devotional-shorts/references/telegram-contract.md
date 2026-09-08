# Telegram 보고·승인 계약

Telegram 연결이 필요한 작업에서 이 문서를 읽고 플러그인 루트의 `scripts/telegram_bot.py`와 `scripts/approval_workflow.py`만 사용한다. 명령 예시는 플러그인 루트 기준이다. 구현은 [Telegram Bot API 공식 문서](https://core.telegram.org/bots/api)의 HTTPS JSON·multipart 요청과 `getUpdates` 계약을 따른다.

## 설정

다음 값은 사용자 로컬 환경변수에서만 읽는다.

- `TELEGRAM_BOT_TOKEN`: BotFather가 발급한 봇 토큰
- `TELEGRAM_CHAT_ID`: 전용 그룹의 정수 ID
- `TELEGRAM_APPROVER_IDS`: 쉼표로 구분한 승인 담당자 사용자 ID

값을 파일, 보고서, 프롬프트, 오류 또는 답변에 복사하지 않는다. 환경변수가 없거나 형식이 틀리면 네트워크를 호출하지 않는다.

## 사전 점검

```bash
python3 scripts/telegram_bot.py preflight
```

`getMe`, `getWebhookInfo`, `getChat`, `getChatMember`만 호출하며 테스트 메시지를 보내지 않는다. outgoing webhook이 있으면 `getUpdates` 승인 조회와 충돌하므로 중단한다. 봇·그룹 접근, 명시적으로 확인 가능한 메시지·문서·사진 권한, 담당자 수를 확인한다. API가 권한을 완전히 확정하지 못하면 실제 전송 단계에서 다시 확인한다.

## 새 보고와 재전송

로컬 `DRAFT`와 `report.md` 검증이 끝난 뒤 실행한다.

```bash
python3 scripts/telegram_bot.py send-report \
  --project-root <project-root> \
  --job-id <job-id>
```

요약 메시지 다음에 전체 보고서 파일을 보내고 요약 ID를 현재 승인 기준으로 저장한다. 같은 성공 명령을 반복하면 네트워크를 호출하지 않는다. 요약 뒤 파일 전송이 실패하면 요약 체크포인트를 보존하므로 같은 명령을 다시 실행했을 때 파일만 재시도한다.

`보고 재전송 [작업 ID]`는 다음 통합 명령으로 처리한다. 작업 ID가 없으면 승인 대기 작업이 정확히 하나일 때만 선택한다.

```bash
python3 scripts/approval_workflow.py resend-report \
  --project-root <project-root> \
  --job-id <job-id>
```

재전송은 리비전을 올리지 않고 새 요약 ID를 승인 기준으로 만든다. 이전 메시지의 승인과 유효 답장 목록은 무효이며 Telegram 업데이트 커서는 유지한다.

## 승인 확인

`승인 확인 [작업 ID]`에는 저수준 `check-replies`가 아니라 다음 통합 명령을 사용한다.

```bash
python3 scripts/approval_workflow.py check-approval \
  --project-root <project-root> \
  --job-id <job-id>
```

통합 명령은 다음 순서를 한 번 수행한다.

1. 현재 상태와 24시간 승인 창을 먼저 검사한다.
2. 마지막 `update_id` 다음의 업데이트만 조회한다.
3. 대상 그룹, 현재 요약 메시지 직접 답장, 등록 담당자, 텍스트 존재를 검사한다.
4. 유효·무효 답장 원문과 사유를 작업 이벤트에 기록한다.
5. 유효 답장을 개별 분류하고 가장 안전한 결정을 상태에 저장한다.

Telegram 업데이트 큐는 봇 전체에 적용되므로 봇당 승인 대기 작업은 하나만 허용한다. 다른 `SENT_FOR_APPROVAL` 작업이 있으면 새 보고와 승인 확인을 네트워크 호출 전에 중단한다.

분류 우선순위는 `revision_requested` → `hold` → `approved` → `unclear`다.

- `수정해서 진행`, `바꾸면 승인`, 삭제·추가·조정 요구: `revision_requested`
- `보류`, `대기`, `나중에 결정`: `hold`
- `승인합니다`, `그대로 진행해 주세요`, `/approve`, `/approve@봇사용자이름`: `approved`
- `좋아요`, 감사, 질문, 감상, 모호한 긍정: `unclear`

`수정 필요 없이 승인`처럼 수정 부정과 명확한 승인이 함께 있으면 승인으로 분류한다. 그 밖의 충돌·조건부 문장은 더 안전한 분류를 사용한다. 수정·보류·불명확이면 이미지 호출은 0회다. 명확한 승인도 이 단계에서는 상태만 `APPROVED`로 만들며, 이미지 도구는 다음 단계의 콘텐츠 서명·메시지·리비전 게이트를 다시 통과해야 한다.

Privacy Mode가 켜진 그룹에서 명시적 승인을 보낼 때는 현재 요약 메시지에 직접 답장하여 `/approve@봇사용자이름`을 전송한다. 이 명령에 수정 또는 보류 표현이 함께 있으면 기존 안전 우선순위에 따라 각각 `revision_requested` 또는 `hold`로 분류한다.

보고 후 24시간이 지나면 업데이트를 조회하지 않고 승인 대기 상태를 유지한 채 재전송을 안내한다. 같은 `update_id`와 집계 결정을 다시 처리하지 않는다.

## 작업 ID 생략과 상태

작업 ID가 없으면 `SENT_FOR_APPROVAL` 작업이 정확히 하나일 때만 자동 선택한다. 0개 또는 2개 이상이면 상태를 바꾸지 않고 작업 ID를 요구한다.

`작업 상태 [작업 ID]`는 네트워크와 Telegram 설정 없이 실행한다.

```bash
python3 scripts/approval_workflow.py status \
  --project-root <project-root> \
  --job-id <job-id>
```

현재 리비전, 상태, 보고 시각, 승인 결과, 실패 장면 수를 한국어로 표시한다.

## 이미지 전달 경계

`telegram_bot.py send-images`는 `GENERATING` 상태에서 로컬 생성이 완료된 장면만 번호순으로 보낸다. 성공 장면의 메시지 ID는 즉시 저장하고 이미 전달된 장면은 건너뛴다. 이 명령은 승인 판정이나 이미지 생성을 수행하지 않으며, 이미지 생성 워크플로가 승인 게이트를 통과한 뒤에만 호출한다.

## 실패 처리

- 읽기 계열 API의 5xx·네트워크 실패만 한 번 재시도한다. 전송 계열 API는 응답 유실 시 중복될 수 있어 자동 재시도하지 않는다.
- 429는 자동 재시도하지 않고 Telegram의 `retry_after` 값을 복구 안내에 표시한다.
- 토큰은 URL 또는 원시 오류에 포함되어도 `[REDACTED]`로 바꾼다.
- 보고 실패는 로컬 결과를 보존하며 승인 상태를 만들지 않는다.
- 미등록·이전 메시지·다른 그룹 답장은 무효 이벤트로만 남긴다.
- 일부 이미지 전달 실패는 성공 ID를 보존하고 실패 장면만 다음 명령에서 재시도한다.
