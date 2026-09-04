# 헤더·구현 파일의 함수 선언 일치 기준

반환형, 함수의 `const`, 매개변수 수와 자료형·포인터·참조를 정확히 맞춰야 합니다. `FVector`와 `const FVector&`는 같은 선언이 아닙니다.

기본 인자, `virtual`, `override`는 헤더 선언에 두고 `.cpp` 정의에 반복하지 않습니다. 델리게이트 콜백과 `Broadcast()`도 선언된 인자 개수·자료형과 맞춰야 합니다.

함수 형태를 바꿨다면 헤더, 구현, 부모·인터페이스 선언, 모든 호출부와 델리게이트 연결을 함께 확인합니다. 헤더만 바꾸고 기존 호출을 남겨 두지 말아야 합니다.

관련 오류는 `CPP_RETURN_TYPE_MISMATCH`, `CPP_FUNCTION_SIGNATURE_MISMATCH`, `CALLBACK_FUNCTION_POINTER_MISMATCH`, `INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH`, `MULTIFILE_CALLSITE_DRIFT`입니다. 오류 이름만으로 결론내리지 말고 양쪽 선언을 직접 비교합니다.

답변에는 바뀌어야 하는 파일과 실제 검사 결과를 적습니다. 파일만 수정했다면 `Patched`이며 빌드 성공으로 올려 말하지 않습니다.
