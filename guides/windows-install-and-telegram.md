# Windows 설치·Telegram 연결·제작 방식 변경 안내

작성일: 2026-09-09 · 대상 플러그인: devotional-shorts-prep 1.0.1 · 방법론: 1.1.0

이 문서는 GitHub에 게시하거나 Markdown 파일 그대로 배포 파일과 함께 전달할 수 있다. 실제 토큰과 개인 설정은 포함하지 않는다. 아래 명령은 Windows PowerShell 기준이며, 각 단계의 성공을 확인한 뒤 다음 단계로 이동한다. Windows 실기기 실행은 아직 검증하지 않았으므로 5~8단계의 인수 확인이 필요하다.

## 1. 준비물

- Codex를 사용할 수 있는 본인 계정과 Windows 실행 환경
- Python 3 실행 환경, GitHub 방식이면 Git과 저장소 접근 권한
- 본인 전용 Telegram 봇, 보고받을 그룹, 승인 담당자
- 이미지 생성까지 사용할 경우 해당 Codex 작업에 내장 이미지 생성 도구가 제공되는지 확인
- 작업을 저장할 영구 폴더. 예: `C:\DevotionalShorts\work`

Codex 설치만으로 이미지 도구 사용 가능 여부까지 보장되지는 않는다. 도구가 없으면 현재 플러그인은 이미지 생성을 중단하며 다른 API로 자동 전환하지 않는다.

## 2. Python 실행 확인

Windows 시작 메뉴에서 PowerShell을 열고 실행한다.

```powershell
python --version
python -c "import sys; print(sys.executable); print('PYTHON_OK'); print(sys.version)"
```

`Python 3.x`, 실행 파일 경로, `PYTHON_OK`가 나오면 실제 실행 성공이다. 버전 확인만으로 플러그인 호환성 검증까지 끝난 것은 아니다.

`python`을 찾지 못하거나 Microsoft Store만 열리면 다음을 시도한다.

```powershell
py -3 --version
py -3 -c "import sys; print(sys.executable); print('PYTHON_OK'); print(sys.version)"
```

이것만 성공하면 아래 문서의 `python`을 `py -3`으로 바꿔 실행한다. 둘 다 실패하면 Python 공식 배포 경로로 설치하고 PowerShell과 Codex를 완전히 닫았다가 다시 연다. 기존 배포는 macOS/Python 3.9.6에서 검사했으며 다른 버전은 아래 테스트 결과로 확인한다.

Codex에도 다음처럼 요청한다.

> 이 Windows 작업에서 Python의 버전과 실행 파일 경로를 확인해 주세요. python3이 없으면 확인된 python 또는 py -3을 사용해 주세요. WSL과 Windows 실행 환경을 섞지 말고, 플러그인 명령의 인자와 검증 절차는 유지해 주세요.

공식 설명: [Python on Windows](https://docs.python.org/3/using/windows.html).

## 3. GitHub 또는 ZIP으로 설치

### A. GitHub에 게시한 경우

공개 저장소이므로 지인 계정 초대 없이 실행할 수 있다.

```powershell
git --version
codex --version
codex plugin marketplace add eundeo/devotional-shorts-prep --ref v1.0.1
codex plugin add devotional-shorts-prep@devotional-shorts
codex plugin list --json --marketplace devotional-shorts
```

원격 저장소에 `v1.0.1` 태그가 공개되어 있어 별도 초대 없이 사용할 수 있다. 쓰기 작업이 아니라 설치만 할 때는 GitHub 토큰을 저장소 URL에 넣지 않는다.

`codex` 명령이 없으면 Codex CLI 설치 또는 PATH 설정이 필요하다. 플러그인 하위 명령이 없으면 사용하는 CLI 버전을 확인한다. 현재 배포 검증에 사용한 CLI는 0.153.1이다.

공식 안내: [플러그인 설치](https://learn.chatgpt.com/docs/plugins), [마켓플레이스 등록·공유](https://developers.openai.com/plugins/build/plugins).

### B. GitHub 없이 ZIP으로 받은 경우

다음 네 파일을 받는다: 플러그인 ZIP, `.zip.sha256`, `marketplace.json`, 이 안내서.

PowerShell에서 다운로드 폴더로 이동한 뒤 체크섬을 확인한다. 실제 폴더로 바꾼다.

```powershell
Set-Location 'C:\DevotionalShorts\download'
$expected = ((Get-Content '.\devotional-shorts-prep-1.0.1.zip.sha256' -Raw).Trim() -split '\s+')[0]
$actual = (Get-FileHash '.\devotional-shorts-prep-1.0.1.zip' -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw '체크섬 불일치: 설치 중단' }
'CHECKSUM_OK'
```

`CHECKSUM_OK` 이후 처음 설치하는 빈 전용 폴더에 해제한다. 기존 설치를 덮어쓰는 데 이 명령을 사용하지 않는다.

```powershell
New-Item -ItemType Directory -Path 'C:\DevotionalShorts\marketplace\plugins' -Force | Out-Null
New-Item -ItemType Directory -Path 'C:\DevotionalShorts\marketplace\.agents\plugins' -Force | Out-Null
Expand-Archive '.\devotional-shorts-prep-1.0.1.zip' -DestinationPath 'C:\DevotionalShorts\marketplace\plugins'
Copy-Item '.\marketplace.json' 'C:\DevotionalShorts\marketplace\.agents\plugins\marketplace.json'
codex plugin marketplace add 'C:\DevotionalShorts\marketplace'
codex plugin add devotional-shorts-prep@devotional-shorts
codex plugin list --json --marketplace devotional-shorts
```

목록에서 이름과 버전 `1.0.1`, `installed: true`, `enabled: true`를 확인한다. 설치 후 새 Codex 작업을 시작한다.

## 4. 작업 폴더와 설치본 확인

```powershell
New-Item -ItemType Directory -Path 'C:\DevotionalShorts\work' -Force | Out-Null
```

이후 작업은 위 폴더에서 진행한다. 작업 결과는 `work\jobs` 아래에 저장하며 설치 캐시에 저장하지 않는다.

설치 명령이 표시한 `Installed plugin root`를 기록한다. 아래 예시의 값을 그 실제 경로로 바꾼다. 사용자명이나 버전 캐시 경로를 추측하지 않는다.

```powershell
$PluginRoot = '실제 Installed plugin root 경로'
Test-Path "$PluginRoot\scripts\telegram_bot.py"
python "$PluginRoot\scripts\validate_architecture.py"
python "$PluginRoot\scripts\distribution.py" audit
python -m unittest discover -s "$PluginRoot\scripts" -p 'test_*.py'
```

파일 존재 확인은 `True`, 검사는 통과, 1.0.1 테스트는 71개 `OK`가 기준이다. 실패하면 오류를 해결한 뒤 다음 단계로 간다. 검사 통과 전에는 Windows 운영 완료로 표시하지 않는다.

## 5. 지인 전용 Telegram 봇과 그룹 만들기

1. Telegram 공식 인증 계정 `@BotFather`를 열고 `/newbot`을 보낸다.
2. 표시 이름과 봇 사용자 이름을 지정하고 발급된 토큰을 본인만 보관한다.
3. 보고용 새 그룹을 만들고 해당 봇과 승인 담당자를 초대한다.
4. 봇에 메시지·문서·이미지 전송을 허용한다. 불필요한 관리자 권한은 부여하지 않는다.
5. 봇은 이 설치 전용으로 사용한다. 다른 컴퓨터·프로젝트·자동화가 같은 봇의 업데이트를 조회하지 않게 한다.
6. 그룹에서 각 승인 담당자가 `/setup@새봇사용자이름`을 한 번 보낸다. 실제 봇 사용자 이름으로 바꾼다. 플러그인의 기능 명령이 아니라 본인 ID를 확인하기 위한 메시지다.

일반 문장이 보이지 않는다고 바로 privacy mode를 끄지 않는다. 봇을 지정한 명령과 봇 메시지에 대한 직접 답장으로 운영한다. [Telegram 봇 만들기](https://core.telegram.org/bots/tutorial), [봇이 수신하는 메시지](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get).

## 6. 토큰·그룹 ID·승인자 ID 설정

아래 입력은 본인이 PowerShell에서 실행한다. Codex 대화에 실제 값을 붙이지 않는다. 토큰을 명령문에 직접 넣지 않고 숨김 입력으로 받는다.

```powershell
$secret = Read-Host 'BotFather 토큰 입력' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $env:TELEGRAM_BOT_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
Remove-Variable secret, ptr
```

그룹·사용자 ID는 숫자이며 `@사용자이름`이나 전화번호가 아니다. 다음은 신규 전용 봇 설정 때 한 번만 실행하는 조회다. 운영 중 승인 답장을 확인하는 용도로 재사용하지 않는다. 봇 API는 토큰을 URL 경로에 사용하므로 요청 URL과 오류 전체를 기록하거나 공유하지 않는다.

```powershell
try {
    $hook = Invoke-RestMethod -Uri ("https://api.telegram.org/bot{0}/getWebhookInfo" -f $env:TELEGRAM_BOT_TOKEN) -ErrorAction Stop
    if (-not $hook.ok -or $hook.result.url) {
        throw 'webhook 또는 응답 오류'
    }
    $updates = Invoke-RestMethod -Uri ("https://api.telegram.org/bot{0}/getUpdates" -f $env:TELEGRAM_BOT_TOKEN) -ErrorAction Stop
    if (-not $updates.ok) { throw '조회 실패' }
    $updates.result | ForEach-Object {
        if ($_.message -and $_.message.text -like '/setup@*') {
            [PSCustomObject]@{
                GroupName = $_.message.chat.title
                ChatId = $_.message.chat.id
                ApproverName = $_.message.from.first_name
                ApproverId = $_.message.from.id
            }
        }
    } | Format-Table
} catch {
    Write-Host '조회 실패: 토큰, 네트워크, 전용 봇의 webhook 설정을 확인하세요. 원본 오류는 공유하지 마세요.'
}
```

원하는 그룹 이름과 실제 승인 담당자의 이름을 대조해 숫자를 선택한다. 그룹 ID는 보통 음수이므로 부호를 보존한다. 결과가 없으면 봇 초대와 명령의 봇 사용자 이름을 확인하고 그룹에서 새 명령을 보낸 뒤 다시 조회한다. 활성 webhook이 있으면 기존 연동을 임의 삭제하지 말고 전용 새 봇을 사용한다.

```powershell
$env:TELEGRAM_CHAT_ID = Read-Host '대상 그룹 숫자 ID 입력'
$env:TELEGRAM_APPROVER_IDS = Read-Host '승인 담당자 숫자 ID 입력 (여러 명이면 쉼표로 구분)'
Remove-Variable updates, hook -ErrorAction SilentlyContinue
python "$PluginRoot\scripts\telegram_bot.py" preflight
```

전송 권한, 승인자 수, `webhook_configured: false`, `messages_sent: 0`이 포함된 성공 결과를 확인한다. 이 검사는 메시지를 보내지 않는다.

### Codex에 같은 환경 전달

위 설정은 현재 PowerShell과 여기서 새로 시작하는 자식 프로세스에만 적용된다. 이미 열려 있는 Codex에는 자동 전달되지 않는다. 가장 명확한 방법은 이 PowerShell에서 작업 폴더로 이동한 뒤 Codex CLI를 시작하는 것이다.

```powershell
Set-Location 'C:\DevotionalShorts\work'
codex
```

데스크톱 앱을 사용할 경우 Windows의 사용자 환경변수 설정 화면에 같은 세 값을 등록하고 앱을 완전히 종료·재시작한다. 전달이 안 되면 로그아웃·로그인 후 다시 확인한다. 사용자 환경변수는 암호 저장소가 아니므로 공유 Windows 계정에 토큰을 저장하지 않는다. 영구 저장을 원하지 않으면 위 일회성 PowerShell 방식을 사용한다.

Codex에는 변수 값 출력 없이 설정 여부를 확인하고 `preflight`를 실행하도록 요청한다. `.env.example`을 복사하는 것만으로 설정이 적용되지는 않는다. 네트워크 권한 요청이 나오면 해당 Telegram 작업의 접근을 허용한 뒤 재검사한다.

## 7. 원고·보고·승인 인수

먼저 실제 성경 본문 전문을 준비해 다음처럼 요청한다.

> devotional-shorts 스킬로 아래 본문의 제작안을 작성해 주세요. 이번에는 로컬 보고서까지만 만들고 Telegram 전송과 이미지 생성은 실행하지 마세요. 설치본의 방법론 1.1.0을 사용하고 작업 ID, 보고서 경로, 품질 평가를 알려주세요. 본문: [실제 전문]

본문 해석, 핵심 문장, 적용, CTA, 장면 계획을 사람이 검토한다. 통과하면 같은 작업을 대상으로 요청한다.

> 방금 작업의 보고서를 Telegram 테스트 그룹에 전송해 주세요. 실제 요약 메시지와 첨부 보고서의 전송 성공 여부를 확인해 주세요. 아직 이미지는 생성하지 마세요.

그룹에 요약과 `report.md`가 실제 도착했는지 확인한다. 등록된 담당자가 최신 요약 메시지에 직접 답장으로 `승인`을 보낸다. 이미지 도구와 생성량을 확인한 뒤 Codex에 `승인 확인 [작업 ID]`를 요청한다. 이 명령은 승인 성공 후 이미지 단계로 이어질 수 있으므로 이미지 실행 준비가 된 뒤 사용한다.

승인 확인은 상시 자동 감시가 아니다. 24시간이 지난 보고는 `보고 재전송 [작업 ID]` 후 새 메시지에 다시 승인한다. 봇당 승인 대기 작업·조회 실행자는 하나만 유지한다. 전송 오류 뒤에는 실제 그룹 도착 여부를 먼저 확인하고 재전송한다.

## 8. 이미지 인수와 완료

현재 리비전 승인 뒤 이미지 도구를 호출하고 결과 파일을 저장·Telegram 전달하는 흐름이 구현되어 있다. 다음을 확인한다.

- 승인 전 이미지가 생성되지 않았는가
- 승인한 장면의 프롬프트를 그대로 사용했는가
- 세로 이미지, 장면 내용, 인물, 시대 배경, 자막 여백이 적절한가
- 글자·워터마크·인체 오류 등 수정할 문제가 없는가
- 생성 수·실패 수·Telegram 전달 수가 실제 결과와 일치하는가

자동 검사는 파일 형식·크기·세로형·해시를 확인한다. 이미지가 장면을 정확하게 묘사하는지까지 보장하는 시각 판정기는 아니다. 이미지 생성에는 계정 사용량이 소모될 수 있으며 많은 장면이면 전체 실행 전에 장면 수를 확인한다.

## 9. 기본 제작 규칙과 정확성의 범위

원본 플러그인을 수정 없이 설치하면 방법론 1.1.0과 다음 규칙이 전달된다.

| 구분 | 기본 규칙 |
| --- | --- |
| 입력·해석 | 본문 전문 필수, 본문 사실·해석·추론·적용 구분, 근거 없는 내면 동기 단정 금지 |
| 묵상 | 배경·핵심 해석·핵심 문장·오늘의 적용·적용 질문 |
| 후보 | 오프닝 10개, 썸네일 10개를 평가해 최종 1개와 대안 3개씩 선정 |
| 원고 | 고정 순서, 다·나·까 문체, 짧은 기도, 지정된 CTA |
| 분량 | 목표 100~120초, 사유 있는 예외 150초 이내 |
| 장면 | 원고 문장을 누적 20자 기준으로 분할. 정확히 20자마다 자르는 방식은 아님 |
| 이미지 | 기본 9:16, 따뜻한 색감·중간 밝기·영화적 질감, 고대 근동/현대 한국 배경, 글자·워터마크 금지 |
| 승인 | 현재 원고·장면 프롬프트 승인 후 생성, 변경하면 새 리비전·재승인 |

원고의 의미 품질은 모델 평가와 사람 검토가 필요하다. 71개 테스트 통과는 도구와 검증 규칙의 동작 증거이며, 모든 성경 해석이나 이미지가 매번 100% 정확하다는 보장은 아니다. 같은 입력도 모델·계정·생성 시점에 따라 표현과 이미지가 달라질 수 있다. 영상 편집, 음성, 자막 합성, 최종 MP4 제작, YouTube 업로드는 현재 기능이 아니다.

## 10. 지인이 제작 방식을 바꾸는 방법

### 이번 작업에만 바꾸기

대상 독자, 강조점, 화풍, 인물, 특정 장면 구도는 본문과 함께 요청할 수 있다. 예: “이번에는 청년 대상의 적용으로, 이미지는 수채화 스타일로 만들어 주세요.” 고정 CTA나 분량처럼 검증기에 고정된 규칙과 충돌하면 작업별 요청만으로 변경이 완료된다고 간주하지 않는다.

승인 후 원고·프롬프트를 바꾸려면 새 리비전을 만들고 다시 승인받는다. 이미 승인된 프롬프트를 생성 직전에 몰래 수정하지 않는다.

### 앞으로의 기본 방식을 바꾸기

지인 전용 소스 복사본 또는 GitHub fork에서 변경한다. 설치 캐시를 직접 편집하면 업데이트 때 덮어써질 수 있다.

1. 변경할 규칙을 문장으로 확정한다.
2. `skills/devotional-shorts/references/method.md`와 해당 생성·이미지 계약을 수정한다.
3. 원고 구조·CTA·길이·장면 분할 등은 `scripts/content_workflow.py`의 검사 규칙과 관련 테스트도 함께 검토한다.
4. 방법론이 바뀌면 방법론 버전, 패키지가 바뀌면 플러그인 버전을 올린다. 단순 작업별 선택은 기본 방법론 변경과 구분한다.
5. 전체 검사와 대표 본문·이미지 검토 후 지인 전용 마켓플레이스에서 재설치한다.
6. 새 Codex 작업에서 새 규칙을 확인한다. 기존 작업은 저장된 방법론 스냅샷을 유지한다.

별도 fork의 변경은 원저자의 설치에 자동 반영되지 않는다. 원본 업데이트를 받을 때 지인의 변경과 충돌하는지 검토한다. 원본과 동시에 설치할 별도 이름을 원하면 코드의 이름 검증도 함께 수정해야 하므로 폴더 이름만 바꾸지 않는다.

## 11. 배포 담당자의 GitHub 게시 절차

배포 원격은 공개 저장소 `eundeo/devotional-shorts-prep`이다. 지인은 별도 초대 없이 설치할 수 있다.

1. GitHub 저장소 이름과 공개 범위를 확인한다. 현재 저장소는 누구나 읽고 설치할 수 있는 공개 저장소다.
2. `.agents/plugins/marketplace.json`과 `plugins/devotional-shorts-prep/`의 상대 경로를 보존한다. 이 안내서는 `guides/`에 함께 올린다.
3. Git 추적 파일 전체와 기존 커밋 이력을 검토한다. 배포 ZIP 감사가 전체 저장소 이력 공개 검토를 대신하지는 않는다. 실제 토큰·설정, `jobs/`, 원본 자료·개인 이미지 파일은 게시하지 않는다.
4. 검증된 릴리스 소스와 로컬 `v1.0.1` 태그를 게시한다. 원본 방법론 바이트가 바뀌지 않도록 Git 줄바꿈 설정을 확인하고 체크섬 검증된 ZIP도 함께 제공한다.
5. GitHub Release에 ZIP·체크섬·marketplace.json과 이 안내서의 사본을 첨부한다. `dist/`는 Git 제외이므로 별도의 릴리스 자산 첨부가 필요하다.
6. 이 안내서는 기존 `v1.0.1` 태그 이후에 추가됐으므로 기본 브랜치 또는 릴리스 첨부 문서로 안내한다.
7. 지인의 Windows 환경에서 공개 저장소 접근과 설치·Telegram·이미지 인수를 완료한다.

GitHub에 올리는 것은 설치 파일 배포이며 Telegram 봇을 GitHub가 실행해 주는 것은 아니다. 봇 설정과 실행은 지인 컴퓨터에서 이뤄진다.

## 12. 인수 결과 기록

| 항목 | 기록 |
| --- | --- |
| 인수 날짜 / 담당자 | 미확인 |
| Windows / Python / Codex 버전 | 미확인 |
| 플러그인 / 방법론 | 목표 1.0.1 / 1.1.0 |
| 설치본 테스트·배포 검사 | Windows 실행 대기 |
| 로컬 보고서 검토 | 대기 |
| Telegram preflight·보고·승인 | 대기 |
| 이미지 도구·생성·시각 검토·전달 | 대기 |
| 영구 작업 폴더·백업 담당자 | 미지정 |

완료 결과만 기입하고 토큰·실제 환경변수 값은 기록하지 않는다. 작업 데이터에는 담당자 답장과 사용자 ID가 포함될 수 있으므로 비공개 보관한다. 기본 보존은 전달 완료 후 90일, 최소 주간 및 완료 직후 백업, 월간 복원 검증을 따른다. 조직 정책이 있으면 해당 기준을 적용한다.
