# 지인 Codex용 묵상 쇼츠 플러그인 인수 실행서

> 대상: Windows + Codex CLI  
> 공개 저장소: <https://github.com/eundeo/devotional-shorts-prep>
> 인수 버전: `v1.0.1`  
> 플러그인/스킬: `devotional-shorts-prep` / `devotional-shorts`

이 파일은 설명서이자 **지인의 Codex가 설치부터 실제 인수 시험까지 순차 실행하기 위한 지시서**다. 지인은 이 파일과 마지막 장의 첫 메시지를 Codex에 함께 전달한다.

## 공개 배포 주소

- 저장소 웹 주소: <https://github.com/eundeo/devotional-shorts-prep>
- Git 복제 주소: `https://github.com/eundeo/devotional-shorts-prep.git`
- `v1.0.1` 릴리스: <https://github.com/eundeo/devotional-shorts-prep/releases/tag/v1.0.1>
- 이 실행서의 최신 원본: <https://github.com/eundeo/devotional-shorts-prep/blob/main/guides/friend-codex-handoff.md>

저장소는 공개 상태이므로 읽기와 설치를 위해 GitHub 협업자 초대나 개인 액세스 토큰이 필요하지 않다.

## 1. Codex 실행 원칙

Codex는 먼저 이 문서를 끝까지 읽고 다음을 지킨다.

1. 번호 순서대로 실행하고 실제 결과로 성공 기준을 확인한 뒤 다음 단계로 간다.
2. GitHub 로그인, BotFather 조작, 비밀정보 입력, 원고·이미지 검토와 비용 승인은 사용자에게 한 번에 한 행동씩 요청한다.
3. Telegram 토큰은 채팅에 입력하도록 요구하지 않는다. 사용자가 PowerShell에서 숨김 입력한다.
4. 토큰, 전체 Telegram ID, 자격 증명을 로그나 최종 보고서에 노출하지 않는다.
5. 설치 캐시와 기본 규칙은 사용자가 개발을 명시하지 않는 한 수정하지 않는다.
6. 생성 결과는 `C:\DevotionalShorts\work`에 두어 설치 원본과 분리한다.
7. 전송 결과가 모호하면 상태를 먼저 대조하고 중복 전송·생성을 하지 않는다.
8. 자동 검사만으로 해석과 이미지가 정확하다고 단정하지 않고 사람 검토를 받는다.
9. 마지막에는 12장의 형식으로 증거와 미해결 항목을 보고한다.

## 2. 완료 범위

목표는 다음 네 가지를 실제 확인하여 `FULL_READY` 상태를 얻는 것이다.

- Python, Git, Codex가 Windows에서 실행됨
- 공개 저장소의 `v1.0.1`을 검증하고 플러그인을 설치·활성화함
- 지인 전용 Telegram 봇·그룹·승인자를 연결하고 보고·승인을 시험함
- 실제 본문 한 건에서 원고 검토, 승인 후 이미지 생성·전달까지 확인함

제공 기능은 본문 분석, 묵상·원고·장면 설계, 로컬 보고서, Telegram 보고·승인, 승인된 이미지 생성·전달이다. 최종 MP4 편집, 음성·자막·음악 합성, YouTube 게시 기능은 포함하지 않는다.

## 3. 사전 준비

Codex는 다음을 확인하고 부족한 것만 사용자에게 요청한다.

- 공개 GitHub 저장소에 인터넷으로 접근할 수 있음
- Windows PowerShell과 인터넷을 사용할 수 있음
- Codex 설치·로그인 권한이 있음
- Telegram 계정으로 전용 봇과 비공개 그룹을 만들 수 있음
- 인수 시험용 **절 번호를 포함한 성경 본문 전체**가 준비됨

저장소: <https://github.com/eundeo/devotional-shorts-prep>

저장소는 공개 상태이므로 협업자 초대는 필요 없다. 접근할 수 없다면 URL, 인터넷 연결, Git 설정을 순서대로 확인한다.

## 4. Windows 환경과 배포본 검증

PowerShell에서 실행한다.

```powershell
python --version
python -c "import sys; print(sys.executable); print('PYTHON_OK'); print(sys.version)"
git --version
codex --version
```

`python`이 실패하면 아래를 확인한다. 이후 모든 Python 명령에는 성공한 방식을 사용한다.

```powershell
py -3 --version
py -3 -c "import sys; print(sys.executable); print('PYTHON_OK'); print(sys.version)"
```

버전이 나오지 않고 Microsoft Store만 열리면 Python이 준비되지 않은 것이다. 필요할 때만 [Python](https://www.python.org/downloads/windows/), [Git](https://git-scm.com/download/win), [Codex](https://developers.openai.com/codex/cli) 공식 안내로 설치한다.

저장소를 고정 버전으로 받는다.

```powershell
New-Item -ItemType Directory -Force -Path C:\DevotionalShorts | Out-Null
New-Item -ItemType Directory -Force -Path C:\DevotionalShorts\work | Out-Null
git ls-remote https://github.com/eundeo/devotional-shorts-prep.git
git clone --branch v1.0.1 --single-branch https://github.com/eundeo/devotional-shorts-prep.git C:\DevotionalShorts\source
Set-Location C:\DevotionalShorts\source
git describe --tags --exact-match
git status --short
```

GitHub 로그인이 필요하면 사용자가 인증 화면을 완료한다. 토큰을 채팅이나 URL에 넣지 않는다. `source`가 이미 있으면 삭제·덮어쓰기 전에 저장소와 변경 사항을 확인하고, 필요하면 다른 폴더에 복제한다. 태그는 `v1.0.1`, 상태 출력은 비어 있어야 한다.

다음 검사를 실행한다. `py -3` 환경이면 `python`을 `py -3`으로 바꾼다.

```powershell
python plugins\devotional-shorts-prep\scripts\validate_architecture.py
python plugins\devotional-shorts-prep\scripts\distribution.py audit
python -m unittest discover -s plugins\devotional-shorts-prep\scripts -p "test_*.py"
python scripts\validate_project.py
```

성공 기준은 아키텍처·배포 감사·프로젝트 검증 통과, 테스트 `Ran 71 tests`와 `OK`, 플러그인 `1.0.1`, 방법론 `1.1.0`, 스키마 `1`, 배포 파일 `28`이다. 수치가 다르면 임의로 성공 처리하지 말고 태그와 실제 출력을 보고한다.

## 5. 플러그인 설치와 새 세션 전환

검증한 로컬 저장소를 등록하고 설치한다.

```powershell
codex plugin marketplace add C:\DevotionalShorts\source
codex plugin add devotional-shorts-prep@devotional-shorts
codex plugin list --json --marketplace devotional-shorts
```

마켓플레이스 `devotional-shorts`, 플러그인 `devotional-shorts-prep`, 버전 `1.0.1`, enabled를 확인한다. 이미 등록되어 있으면 사용자 설정을 보존하며 현재 버전과 상태를 먼저 검사하고 무조건 제거하지 않는다.

설치된 플러그인은 **새 Codex 세션**에서 로드된다. 현재 Codex는 결과를 `C:\DevotionalShorts\work\installation-checkpoint.md`에 기록한 뒤 사용자가 다음 순서로 재시작하도록 안내한다.

1. 현재 Codex 종료
2. 다음 장의 Telegram 환경변수 설정
3. 같은 PowerShell에서 `C:\DevotionalShorts\work`로 이동해 `codex` 실행
4. 이 파일을 다시 주고 “체크포인트를 확인해 6단계부터 계속”이라고 요청

공식 참고: [플러그인 개발·관리](https://developers.openai.com/plugins/), [플러그인 사용](https://learn.chatgpt.com/docs/plugins)

## 6. 지인 전용 Telegram 준비와 비밀정보 입력

사용자가 Telegram에서 직접 수행한다.

1. 공식 `@BotFather`에 `/newbot`을 보내 전용 봇을 만든다.
2. 토큰은 비밀번호 관리자에 보관한다.
3. 비공개 보고 그룹을 만들고 봇과 실제 승인자만 초대한다.
4. 그룹에서 `/setup@봇사용자명`을 한 번 보낸다.
5. 승인은 나중에 봇의 **최신 요약 메시지에 직접 답장**하여 `승인`이라고 보낸다.

BotFather의 그룹 개인정보 보호 모드는 끌 필요가 없다. 명시적 명령과 봇 메시지 직접 답장을 사용한다. 같은 토큰으로 다른 프로그램이 동시에 `getUpdates`를 실행하지 않게 한다.

Codex는 토큰을 묻지 않고 사용자가 PowerShell에서 직접 숨김 입력하게 한다.

```powershell
$TelegramSecret = Read-Host "Telegram bot token" -AsSecureString
$TelegramPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($TelegramSecret)
try { $env:TELEGRAM_BOT_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($TelegramPointer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($TelegramPointer) }
Remove-Variable TelegramSecret, TelegramPointer
```

`/setup` 전송 뒤 토큰을 출력하지 않는 다음 조회로 그룹과 승인자를 찾는다.

```powershell
$Updates = Invoke-RestMethod -Uri ("https://api.telegram.org/bot{0}/getUpdates" -f $env:TELEGRAM_BOT_TOKEN)
$Updates.result | ForEach-Object {
  $Message = if ($_.message) { $_.message } else { $_.callback_query.message }
  $Sender = if ($_.message) { $_.message.from } else { $_.callback_query.from }
  [PSCustomObject]@{ ChatTitle=$Message.chat.title; ChatId=$Message.chat.id; UserName=$Sender.username; UserId=$Sender.id; Text=$Message.text }
} | Format-Table -AutoSize
```

사용자가 결과의 전용 그룹 `ChatId`와 승인자 `UserId`를 직접 설정한다. 여러 승인자는 쉼표로 구분한다.

```powershell
$env:TELEGRAM_CHAT_ID = "확인한_그룹_ChatId"
$env:TELEGRAM_APPROVER_IDS = "승인자_UserId"
[PSCustomObject]@{
  TokenSet=-not [string]::IsNullOrWhiteSpace($env:TELEGRAM_BOT_TOKEN)
  ChatIdSet=-not [string]::IsNullOrWhiteSpace($env:TELEGRAM_CHAT_ID)
  ApproversSet=-not [string]::IsNullOrWhiteSpace($env:TELEGRAM_APPROVER_IDS)
}
Set-Location C:\DevotionalShorts\work
codex
```

세 값이 `True`인지 확인하되 실제 값은 출력하지 않는다. 이 값은 현재 PowerShell과 여기서 시작한 Codex에만 전달된다. Windows 사용자 환경변수로 영구 등록할 수 있지만 평문 환경변수는 비밀 저장소가 아니므로 첫 시험에는 위 임시 방식을 권장한다.

## 7. Telegram 사전 점검

새 세션에서 이 문서와 체크포인트를 읽고 실행한다.

```powershell
python C:\DevotionalShorts\source\plugins\devotional-shorts-prep\scripts\telegram_bot.py preflight
```

봇·그룹 인증, 승인자 1명 이상, webhook 없음, `messages_sent: 0`을 확인한다. webhook이 있으면 임의 삭제하지 말고 기존 사용 주체를 사용자에게 확인한다. 이 사전 점검은 실제 메시지를 보내지 않아야 한다.

## 8. 첫 원고를 로컬에서 생성·검토

사용자가 Codex에 보낸다.

```text
devotional-shorts 스킬로 아래 성경 본문 전체를 사용해 묵상 쇼츠 준비 작업을 진행해 주세요.
이번에는 Telegram 전송 없이 로컬 보고서까지만 생성하고, 결과와 작업 ID를 알려주세요.

[절 번호를 포함한 성경 본문 전체]
```

기본 생성 규칙은 묵상 필드 5개, 점수화한 오프닝·썸네일 각 10개, 핵심 묵상 1개와 보조 3개, 기본 100~120초(필요 시 최대 150초), `~다/~나/~까` 혼합과 CTA, 약 20자 누적 기준 문장 분할, 9:16 장면 프롬프트다.

사람은 본문 인용·문맥·해석, 적용의 과장 여부, 오프닝·썸네일의 왜곡 여부, 낭독 시간·말투, CTA, 장면의 시대·문화·교단상 오해 가능성을 검토한다. 테스트 71개는 워크플로 계약을 검증하지만 새 본문 해석과 모든 이미지의 정확성을 보증하지 않는다.

성공 기준은 작업 ID, 작업 폴더, 전체 보고서가 존재하고 사람이 초안을 승인하거나 수정사항을 제시한 것이다.

## 9. Telegram 보고와 승인

검토 후 사용자가 요청한다.

```text
작업 [작업 ID]의 현재 전체 보고서를 확인해 주세요. Telegram 사전 점검이 통과한 경우에만 지인 전용 그룹으로 승인 요청을 1회 전송하고 결과를 보고해 주세요.
```

그룹에 요약 1건과 전체 보고서 파일이 도착하고 로컬 상태가 일치해야 한다. 수정 시 기존 작업을 새 리비전으로 갱신하고 검증 보고서를 다시 만든 뒤 재승인한다. 이전 승인 스냅샷과 새 초안을 섞지 않는다.

승인자는 봇의 **가장 최근 요약 메시지에 직접 답장**하여 `승인`이라고 보낸다. 새 메시지, 다른 작업 답장, 미등록 계정은 승인으로 처리하지 않는다. 그 후 사용자가 요청한다.

```text
승인 확인 [작업 ID]
```

이는 이미지 생성 시간·비용을 인지하고 진행할 때만 실행한다. 승인 대기 후 24시간이 지났다면 전체 보고서를 다시 확인하고 `보고 재전송 [작업 ID]` 후 새 메시지에서 승인받는다.

## 10. 승인된 이미지 생성·전달

올바른 승인자·최신 메시지·현재 리비전 일치, 이미지 생성 도구 사용 가능, 사용자 진행 의사를 모두 확인한 뒤 요청한다.

```text
승인된 작업 [작업 ID]의 승인 스냅샷을 기준으로 장면 이미지를 생성하고, 각 파일의 검증과 로컬 저장을 완료한 뒤 Telegram으로 전달해 주세요. 실패한 장면은 성공 처리하지 말고 번호와 원인을 알려주세요.
```

사람은 9:16 구도, 본문·장면 일치, 인물·복장·배경 연속성, 손·얼굴·문자·상징 왜곡, 문화적 고정관념, 자막 여백을 확인한다. 자동 검사는 형식·크기·세로 비율·해시 같은 기술 조건만 보장한다.

이미지 도구가 없으면 다른 API 키나 서비스로 임의 우회하지 않는다. 승인 상태를 보존하고 사용 가능한 Codex 환경에서 재개한다. 계획 장면 수, 로컬 파일 수, 검증 통과 수, Telegram 전달 수가 모두 일치해야 한다.

## 11. 지인이 생성 방식을 바꾸는 범위

작업별로 대상 독자, 강조점, 말투, 기본 범위 내 길이, 화풍, 인물·구도, 피할 표현·색·상징을 요청할 수 있다. 본문 근거, 수정 시 재승인, 이미지 전 승인 확인은 생략하지 않는다.

기본 구조를 영구 변경하려면 별도 개발 작업이 필요하다. 저장소를 fork하고 방법론·계약·스크립트·테스트·버전을 함께 변경해 재검증한다. 설치 캐시 직접 수정은 업데이트 때 사라지고 검증 근거도 깨진다.

## 12. 장애 처리와 최종 인수 보고

| 증상 | 처리 |
|---|---|
| Python이 Store만 엶 | `py -3` 확인 후 둘 다 실패하면 공식 설치 요청 |
| 저장소 접근 불가 | URL·인터넷 연결·Git 설정을 확인하고 공개 웹 페이지 접근 여부 점검 |
| 스킬이 안 보임 | 설치·활성 상태 확인 후 새 Codex 세션 시작 |
| 검사·테스트 실패 | 설치를 중단하고 태그와 실제 실패 출력 보고 |
| Telegram `401` | 사용자가 BotFather에서 토큰 확인 |
| `chat not found` | 그룹 `/setup@봇사용자명`과 Chat ID 재확인 |
| webhook 충돌 | 임의 삭제하지 않고 기존 사용 주체 확인 |
| `429` | 서버 재시도 시간을 지키고 중복 전송 금지 |
| 승인이 안 잡힘 | 승인자·직접 답장·최신 메시지·24시간 만료 확인 |
| 이미지 일부 실패 | 성공 파일을 보존하고 실패 장면만 재개 |

Codex는 비밀값을 제외하고 다음 형식으로 마친다.

```markdown
# 묵상 쇼츠 플러그인 지인 PC 인수 결과
- 최종 상태: FULL_READY / TELEGRAM_READY / LOCAL_READY / BLOCKED
- 점검 일시 / Windows / Python / Git / Codex 버전:
- 저장소/태그: eundeo/devotional-shorts-prep / v1.0.1
- 플러그인/방법론/스키마: 1.0.1 / 1.1.0 / 1
- 아키텍처·배포 감사·프로젝트 검증:
- 자동 테스트: 71/71
- 플러그인 설치·활성화 / 새 세션 스킬 인식:
- Telegram 사전 점검 / 보고 도착 / 승인 확인:
- 로컬 작업 ID와 보고서 경로 / 사람의 원고 검토:
- 이미지 계획/생성/검증/전달 수:
- 결과물 위치 / 남은 항목 또는 차단 사유:
```

`LOCAL_READY`는 로컬 초안까지, `TELEGRAM_READY`는 보고·승인까지, `FULL_READY`는 승인 이미지 검증·전달까지 실제 확인한 상태다. 필수 권한·도구·사용자 행동을 기다리거나 실패하면 `BLOCKED`다. MP4와 YouTube 게시까지 되지 않았다면 “쇼츠 영상 제작 완료”라고 표현하지 않는다.

## 13. 지인이 Codex에 보내는 첫 메시지

```text
첨부한 friend-codex-handoff.md를 처음부터 끝까지 읽고, 문서의 원칙과 순서에 따라 제 Windows PC에 묵상 쇼츠 플러그인을 인수해 주세요.

각 단계는 실제 결과로 검증한 뒤 진행하고, 제가 직접 해야 하는 GitHub 로그인, Telegram BotFather 설정, 비밀정보 입력, 원고·이미지 검토와 승인에서는 필요한 행동을 한 번에 하나씩 안내해 주세요. 비밀 토큰을 채팅에 입력하도록 요구하지 마세요.

완료된 단계는 증거를 확인해 건너뛰고, 마지막에는 문서 12장의 형식으로 최종 상태와 미해결 항목을 보고해 주세요.
```
