# 에디터 전용 코드와 게임 실행 코드 분리

실행용 모듈의 공개 헤더에 에디터 전용 자료형을 끌어오지 말아야 합니다. `UnrealEd.h`, `Kismet2/`, `GEditor`, `FEditorDelegates`를 사용한다면 에디터 모듈 또는 필요한 `WITH_EDITOR` 구현 범위를 확인합니다.

에디터 전용 속성은 `WITH_EDITORONLY_DATA` 등 UHT가 지원하는 조건을 확인해야 합니다. `UCLASS`, `UPROPERTY` 같은 선언을 임의의 전처리 조건으로 감싸지 않습니다.

에디터 빌드만 보고 배포 실행도 된다고 결론내리지 말아야 합니다. 관련 보조 경고는 `EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE`입니다.
