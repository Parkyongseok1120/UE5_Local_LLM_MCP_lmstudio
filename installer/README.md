# 설치 코드 안내

이 폴더에서 설치를 시작하지 말아야 합니다. 사용자는 맨 위의 `INSTALL.bat`, `install.sh`, `install.py`를 실행하면 됩니다. 선택 항목은 [설치 안내](../docs/Integrated_Installer.md)에 있습니다.

`install.py`가 실제 설치를 담당합니다. `bootstrap_python.ps1`과 `bootstrap_python.sh`는 Python이 없는 장비에서 실행 환경을 준비한 뒤 같은 설치기로 넘기는 역할입니다. 설치 구성을 따로 구현하는 곳이 아닙니다.

`install.py --build-rag`는 설치기가 관리하는 Python으로 수집과 검색 자료 생성을 직접 실행합니다. PowerShell이나 에디터 실행 없이 동작해야 합니다. 그래프 내보내기 플러그인 설치와 `.uproject` 수정은 여기서 하지 않습니다.

검색 자료는 기본적으로 `~/.evidence-first/indexes/<namespace>/`에 보관합니다. 프로젝트 이름과 실제 경로를 함께 기록하고, 다른 엔진 버전의 자료는 별도 폴더에 둡니다. 이름이 같은 복사본이나 출처가 불명확한 예전 자료를 임의로 합치지 말아야 합니다.

대화 압축기는 파일을 설치하고 목록에 고정만 합니다. LM Studio 채팅의 스위치는 켜지 않으며 기본은 `OFF`로 사용합니다. 활성화 여부는 사용자가 채팅에서 확인해야 합니다.

런타임 다운로드 정보는 [runtime-manifest.json](runtime-manifest.json)이 기준입니다. 버전과 해시가 맞아야 압축을 풀고 실행합니다. 설치 프로세스가 겹치거나 압축 경로가 허용 범위를 벗어나면 진행하지 않습니다.

개발용 검증 도구는 `scripts/installer_support/`에 있습니다. 일반 배포본 사용법과 개발 저장소 전용 명령을 섞지 말아야 합니다.
