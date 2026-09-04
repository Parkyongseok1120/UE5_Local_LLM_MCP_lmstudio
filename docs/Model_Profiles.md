# 모델 설정 항목과 조정 기준

모델은 LM Studio에서 직접 선택하고 불러옵니다. 개발 저장소의 `config/lmstudio_sampling.json`은 설정값을 참고하도록 둔 파일입니다. 이 파일과 아래 보조 명령은 압축 배포본에는 포함하지 않습니다. MCP가 요청마다 모델을 바꾸거나 작업 순서를 지정하는 기능은 아닙니다.

모든 현재 설정은 [같은 기본 지시문](../prompts/lmstudio_direct_model_system.md)을 사용합니다.

## 주요 설정 항목

| 설정 이름 | 뜻 |
|---|---|
| `contextLength` | 모델이 한 번에 참고할 수 있도록 잡는 대화 길이입니다. 길수록 메모리 부담이 커집니다. |
| `contextLengthAlternatives` | 장비에 맞춰 시험해 볼 다른 길이 |
| `quantDefault` | 모델 용량과 정밀도를 줄이는 저장 방식의 기본값 |
| `recommendedParallelRequests` | 동시에 처리할 요청 수 |
| `recommendedSystemPrompt` | 사용할 시스템 지시문 경로 |
| `sampling` | 답변 생성에 쓸 고정 설정입니다. 작업 단계마다 자동으로 바뀌지 않습니다. |
| `writeSafety` | 예전 실행 도구와의 호환용 값입니다. 현재 MCP 쓰기 권한을 주지는 않습니다. |
| `notes` | 해당 모델·장비에서 확인할 점 |

저장소의 `qwen3_8_27b` 예시는 대화 길이 65,536, `Q4_K_M`, 동시 요청 1개입니다. 더 긴 262,144 설정도 있지만 장비에서 직접 확인해야 합니다. 설정에 이름이 있다는 이유만으로 해당 모델의 성능이 검증된 것은 아닙니다.

## 개발 저장소에서 설정 확인

아래 보조 명령은 개발 저장소용이며 압축 배포본에는 포함되지 않습니다.

```powershell
python scripts/load_sampling_preset.py --sampling-profile qwen3_8_27b --show-profile
python scripts/load_sampling_preset.py --model "qwen/qwen3.8-27b" --show-profile
```

모델 파일명으로 설정을 찾을 수 있지만 실제 모델을 불러오거나 바꾸지는 않습니다. `UNREAL_RAG_MODEL_PROFILE`로 보조 명령에서 사용할 설정을 지정할 수도 있습니다. 예전 `--mode`, `--turn` 인자는 경고만 출력하며 값을 바꾸지 않습니다.

설치된 설정은 `~/.lmstudio/config-presets/evidence-first-code-audit.preset.json`에서 확인할 수 있습니다.

## 메모리 부족과 대화 길이 제한 대응

한 번에 요청하는 작업 범위를 줄이고 전체 소스나 빌드 로그를 채팅에 붙이지 말아야 합니다. 필요한 파일과 오류 부분만 읽게 하는 편이 확인하기 쉽습니다.

같은 계열의 모델이라도 실제 GGUF 파일과 양자화 방식에 따라 도구 호출이 달라질 수 있습니다. 불러오기가 되는지, 검색과 읽기가 되는지, 수정 결과를 제대로 확인하는지 순서대로 살펴봐야 합니다. 설정을 바꿀 때는 한 항목씩 비교해야 원인을 알 수 있습니다.

대화 압축기 `codex/unreal-context-compactor`는 기본 `OFF`입니다. 긴 채팅에서 필요할 때 단일 스위치만 켭니다. 압축기가 모델 설정이나 파일 수정 권한을 바꾸지는 않습니다. 이미 대화 한도를 넘겼다면 현재 요청·프로젝트·바꾼 파일·남은 오류를 짧게 적고 새 채팅으로 옮겨야 합니다.
