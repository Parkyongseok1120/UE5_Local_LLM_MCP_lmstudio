# v1.4.0-beta4 변경 사항

이번 시험 배포는 beta3 이후의 컴팩터 런타임 분리, 읽기 예산과 근거 수명 관리 개선을 포함합니다.

## 주요 변경 사항

- 컴팩터의 입력 조립, 예산 관리, 복구, 전달 로직을 모듈로 분리하고 도구 호출 완료와 입력 ID 연결을 보강했습니다.
- LOW/HIGH 예산과 다음 읽기 가능 여부를 구분하고, 실제 입력 측정에 실패하면 실행을 차단합니다. Unreal `maxBytes`와 공개 도구 스키마 제한을 읽기 예약에 반영합니다.
- Git 등 큰 읽기 결과와 Unity 파일의 `byteBudget` 페이지 처리를 개선하고, 근거 identity·실패 판정·archive 참조를 정리했습니다.
- LM Studio SDK 1.5.0의 prediction 종료 상태 패치를 설치 과정에서 적용합니다.
- CI의 LM Studio 설치 모형에 SDK 패치 대상을 추가하고, 컴팩터 통합 테스트에 필요한 Unity 의존성을 설치합니다. OSS 검사에서 빈 파일의 잘못된 검출을 수정하고 기록 파일의 개인 홈 경로를 익명화했습니다.

## 구성별 버전

| 구성 | 버전 |
|---|---|
| 통합 제품 | 1.4.0 beta 4 (`v1.4.0-beta4`) |
| 휴대용 배포 구성 | 2.1.22 |
| Unreal Node MCP | 0.3.23 |
| Unity MCP·Editor Bridge | 1.4.0-beta.4 |
| 근거 검토 서버 | 1.1.1 |
| 대화 압축기 | 0.4.70 / revision 117 |

## 검증 범위

GitHub Actions는 Windows, Ubuntu, macOS의 Node·Unity MCP와 컴팩터, 휴대용 설치, Windows 회귀·반복 검사, 인코딩·OSS 검사 및 깨끗한 패키지 생성을 확인합니다. 자동 검사가 모든 로컬 모델의 컨텍스트 크기나 모든 Unreal·Unity 환경의 실제 실행을 보장하지는 않습니다. 특히 16KB `git_changed_files` 병렬 요청은 86K 모델 컨텍스트에서도 공유 읽기 예산을 초과할 수 있습니다.
