# 묵상 쇼츠 자동화 플러그인 1.0.0 정식 배포 보고서

## 배포 판정

- 정식 ZIP 배포: 완료
- 다른 사용자 설치 가능 상태: 완료
- GitHub 외부 게시: 대상 저장소와 공개 범위 지정 대기
- 기능 변경: 없음
- 라이선스: MIT

## 릴리스 식별 정보

| 항목 | 값 |
| --- | --- |
| 플러그인 | `devotional-shorts-prep` |
| 플러그인 버전 | `1.0.0` |
| 방법론 버전 | `1.1.0` |
| 방법론 SHA-256 | `1684b969e1822d2cd41eb7a485201871339cd04203557ff13e2b684bb6847ae5` |
| 데이터 스키마 | `1` |
| 배포 파일 수 | 27개와 무결성 매니페스트 1개 |
| ZIP SHA-256 | `f35317fe827b3a0d6fc899d405ce151e270a3268962a9a725e7f5dcf40ec9227` |

## 배포 산출물

- [devotional-shorts-prep-1.0.0.zip](../dist/1.0.0/devotional-shorts-prep-1.0.0.zip)
- [SHA-256 체크섬](../dist/1.0.0/devotional-shorts-prep-1.0.0.zip.sha256)
- [독립 마켓플레이스 매니페스트](../dist/1.0.0/marketplace.json)
- [설치 안내](../plugins/devotional-shorts-prep/README.md)
- [변경 이력](../plugins/devotional-shorts-prep/CHANGELOG.md)
- [MIT 라이선스](../plugins/devotional-shorts-prep/LICENSE)

저장소 밖 `/private/tmp/devotional-shorts-release-1.0.0-final.3gFmgj`와 위 정식 출력 폴더에서 각각 생성한 ZIP의 바이트와 SHA-256이 일치했다.

## 검증 결과

| 검증 | 원본 | 격리 설치본 |
| --- | --- | --- |
| Python 단위·통합 테스트 | 69개 통과 | 69개 통과 |
| 아키텍처 검증 | 통과 | 통과 |
| 배포 경계·비밀정보 감사 | 27개 파일 통과 | 27개 파일 통과 |
| 공식 Codex 플러그인 검증 | 통과 | 통과 |
| 공식 Codex 스킬 검증 | 통과 | 통과 |
| 프로젝트 문서·계약 검증 | 통과 | 해당 없음 |

ZIP 자체 검증, 내부 무결성 해시, 위험 ZIP 경로와 비밀정보 부재, 결정적 재생성도 모두 통과했다.

## 격리 설치 인수

- 마켓플레이스: `/private/tmp/devotional-shorts-marketplace-1.0.0-final.9O5r4V`
- 격리 Codex 홈: `/private/tmp/devotional-shorts-codex-home-1.0.0-final.FhBXxi`
- 설치 캐시: `/private/tmp/devotional-shorts-codex-home-1.0.0-final.FhBXxi/plugins/cache/devotional-shorts/devotional-shorts-prep/1.0.0`
- 공식 CLI: `codex plugin marketplace add`와 `codex plugin add devotional-shorts-prep@devotional-shorts` 통과
- 비운영 신규 작업: `ds-20260813-235959-1a0b0c0d / r001 / DRAFT`
- 신규 작업 결과: 방법론 `1.1.0`, 스키마 `1`, 장면 7개, 무결성 통과
- 기존 `0.9.0` 기준 작업: `ds-20260812-163658-c0df7012 / r002 / DELIVERED`, `1.0.0` 설치본으로 무결성 통과

자격 증명 없이 신규 `DRAFT`까지 생성됐다. `preflight`는 네트워크 호출 전에 세 Telegram 환경변수의 누락을 한국어 복구 안내와 종료 코드 `2`로 정상 보고했다.

## 다른 사용자 설치 순서

1. ZIP과 `.zip.sha256`, `marketplace.json`을 같은 폴더에 받는다.
2. `shasum -a 256 -c devotional-shorts-prep-1.0.0.zip.sha256`을 통과시킨다.
3. 전용 마켓플레이스 루트의 `plugins/`에 ZIP을 푼다.
4. `marketplace.json`을 전용 루트의 `.agents/plugins/marketplace.json`에 둔다.
5. `codex plugin marketplace add <마켓플레이스-루트>`를 실행한다.
6. `codex plugin add devotional-shorts-prep@devotional-shorts`를 실행한다.
7. 새 Codex 작업을 시작하고, 자신의 Telegram 환경변수를 설정한 뒤 `preflight`를 실행한다.

전체 명령과 업데이트 절차는 [플러그인 README](../plugins/devotional-shorts-prep/README.md)에 있다.

## 제한사항과 외부 경계

- 실제 Telegram 토큰·그룹 ID·승인자 ID는 배포물에 없으며 사용자별 설정이 필요하다.
- 신규 배포 검증에서는 실제 Telegram 전송과 ImageGen 호출을 하지 않았다. 이 외부 경계는 세션 09의 실제 종단간 인수로 이미 검증됐다.
- 웹 UI, 상시 서버, 데이터베이스, YouTube 업로드, 음성·자막·영상 렌더링은 v1 범위 밖이다.
- GitHub 원격 저장소가 없어 커밋·태그·릴리스 게시를 실행하지 않았다.
