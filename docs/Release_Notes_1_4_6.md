# v1.4.6 릴리스 안내

## Muse Glimmer 30B 지원

LM Studio에서 `meta/muse-glimmer`를 불러와 언리얼 작업 채팅에 사용할 수 있습니다. 검색·읽기·편집처럼 여러 단계의 도구 호출이 이어지는 작업에서 안정성을 우선한다면 Qwen 3.8 27B보다 **Muse Glimmer 30B**를 먼저 권장합니다. [LM Studio 모델 페이지](https://lmstudio.ai/models/meta/muse-glimmer)

개발 저장소에서는 모델 ID와 GGUF 파일명으로 Muse Glimmer 전용 설정을 조회할 수 있습니다. 설치 후에는 LM Studio에서 모델을 직접 선택합니다. [모델 설정과 시작값](Model_Profiles.md)

## 도구 호출과 프로젝트 편집

- 답변 일부를 이미 작성한 뒤 도구 인수가 출력 한도에서 잘리면, 작성한 내용을 보존하고 실행이 끝나지 않았음을 알립니다.
- 파일 편집이 거부되면 실패 원인과 허용된 변경 줄 수를 더 구체적으로 안내합니다. 정확 일치 편집에서는 대상 범위 밖의 기존 파일 바이트를 보존합니다.
- 경로를 생략한 검색은 활성 프로젝트에서 수행합니다. 프로젝트 외부를 가리키는 정션 때문에 범위 안의 검색이나 목록 조회가 중단되지 않도록 개선했습니다.
- 언리얼 설계 후속 질문에 관련 설계 지침을 연결합니다.

## 설치와 버전

Windows에서는 `INSTALL.bat`, Linux와 Apple Silicon macOS에서는 `./install.sh`를 사용합니다. Intel Mac에서 LM Studio를 연결하는 경우 Linux x64 VM의 llmster/LM Link 설치 절차를 따릅니다. [설치 안내](Integrated_Installer.md)

| 구성 | 버전 |
|---|---|
| 통합 제품 | 1.4.6 |
| 포터블 배포 구성 | 2.1.37 |
| Unreal Node MCP | 0.3.33 |
| 대화 압축기 | 0.4.87 / revision 134 |
| Unity MCP·Editor Bridge | 1.4.0 |
| 근거 검토 서버 | 1.1.4 |
