#!/usr/bin/env bash
# End-to-end Intel Mac installer for LM Studio llmster, LM Link, Unreal MCP,
# and the project-local MCP-aware chat launcher.

set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT=""
ENGINE_KIND="unreal"
UNITY_EDITOR=""
ENGINE_ROOT=""
WORKSPACE_ROOT=""
VM_NAME="lmstudio-link"
DEVICE_NAME="Intel Mac LM Link VM"
MODEL="qwen/qwen3.8-27b"
MODEL_ENDPOINT="${LMSTUDIO_CHAT_ENDPOINT:-http://127.0.0.1:1234/v1/chat/completions}"
CPUS="4"
MEMORY="6GiB"
DISK="30GiB"
LIMA_VERSION="2.2.0"
BUILD_RAG=1
SKIP_LOGIN=0
DRY_RUN=0
TEMP_DIR=""

usage() {
  cat <<'EOF'
Intel Mac용 LM Studio 헤드리스 + UE5 MCP 자동 설치

필수:
  --project PATH       .uproject 파일 또는 해당 파일이 있는 프로젝트 폴더
  --engine-root PATH   Unreal Engine 루트 (예: /Users/Shared/Epic Games/UE_5.7)

선택:
  --engine unreal|unity    프로젝트 엔진 (기본: unreal)
  --unity-editor PATH      Unity 프로젝트용 설치된 Editor 실행 파일
  --workspace-root PATH    MCP가 읽을 프로젝트 루트 (기본: .uproject 부모)
  --vm-name NAME           Lima VM 이름 (기본: lmstudio-link)
  --device-name NAME       LM Link 장치 이름
  --model ID               연결 검증 및 채팅에 사용할 모델
  --model-endpoint URL     MCP 호스트에서 접근할 chat/completions URL
  --cpus N                 VM CPU 수 (기본: 4)
  --memory SIZE            VM 메모리 (기본: 6GiB)
  --disk SIZE              VM 디스크 (기본: 30GiB)
  --skip-rag-build         기존 RAG 인덱스를 사용하고 다시 만들지 않음
  --skip-login             LM Studio 로그인을 건너뜀(이미 로그인한 경우에만 권장)
  --dry-run                변경 없이 계획만 출력
  -h, --help               도움말

예:
  ./install-intel-mac.sh \
    --project /path/to/MyGame/MyGame.uproject \
    --engine-root "/Users/Shared/Epic Games/UE_5.7"

LM Studio 계정 로그인이 필요하면 페어링 URL이 표시되며, 브라우저 승인 후
나머지 설치와 검증은 자동으로 계속됩니다.
EOF
}

die() {
  echo "오류: $*" >&2
  exit 1
}

note() {
  echo "[$(date '+%H:%M:%S')] $*"
}

cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --project) PROJECT=${2:?--project requires a path}; shift 2 ;;
    --engine) ENGINE_KIND=${2:?--engine requires unreal or unity}; shift 2 ;;
    --unity-editor) UNITY_EDITOR=${2:?--unity-editor requires a path}; shift 2 ;;
    --engine-root) ENGINE_ROOT=${2:?--engine-root requires a path}; shift 2 ;;
    --workspace-root) WORKSPACE_ROOT=${2:?--workspace-root requires a path}; shift 2 ;;
    --vm-name) VM_NAME=${2:?--vm-name requires a name}; shift 2 ;;
    --device-name) DEVICE_NAME=${2:?--device-name requires a name}; shift 2 ;;
    --model) MODEL=${2:?--model requires an ID}; shift 2 ;;
    --model-endpoint) MODEL_ENDPOINT=${2:?--model-endpoint requires a URL}; shift 2 ;;
    --cpus) CPUS=${2:?--cpus requires a number}; shift 2 ;;
    --memory) MEMORY=${2:?--memory requires a size}; shift 2 ;;
    --disk) DISK=${2:?--disk requires a size}; shift 2 ;;
    --skip-rag-build) BUILD_RAG=0; shift ;;
    --skip-login) SKIP_LOGIN=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "알 수 없는 옵션: $1" ;;
  esac
done

[[ -n "$PROJECT" ]] || die "--project가 필요합니다."
[[ "$ENGINE_KIND" == "unreal" || "$ENGINE_KIND" == "unity" ]] || die "--engine는 unreal 또는 unity입니다."
if [[ "$ENGINE_KIND" == "unreal" ]]; then
[[ -n "$ENGINE_ROOT" ]] || die "--engine-root가 필요합니다."
fi
[[ "$MODEL$MODEL_ENDPOINT$PROJECT$ENGINE_ROOT$UNITY_EDITOR$WORKSPACE_ROOT${LIMACTL_BIN:-}" != *$'\n'* && "$MODEL$MODEL_ENDPOINT$PROJECT$ENGINE_ROOT$UNITY_EDITOR$WORKSPACE_ROOT${LIMACTL_BIN:-}" != *$'\r'* ]] || die "옵션에 줄바꿈을 포함할 수 없습니다."
[[ "$MODEL_ENDPOINT" == http://* || "$MODEL_ENDPOINT" == https://* ]] || die "모델 endpoint는 명시적인 HTTP(S) URL이어야 합니다."
[[ "$VM_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "유효하지 않은 VM 이름: $VM_NAME"
[[ "$CPUS" =~ ^[1-9][0-9]*$ ]] || die "--cpus는 양의 정수여야 합니다."

if [[ "$ENGINE_KIND" == "unity" ]]; then
  [[ -d "$PROJECT/Assets" && -f "$PROJECT/Packages/manifest.json" && -f "$PROJECT/ProjectSettings/ProjectVersion.txt" ]] || die "Unity 프로젝트 루트가 필요합니다."
  PROJECT=$(CDPATH= cd -- "$PROJECT" && pwd)
  PROJECT_DIR=$PROJECT
  ENGINE_ROOT=$PROJECT
else
if [[ -d "$PROJECT" ]]; then
  shopt -s nullglob
  project_matches=("$PROJECT"/*.uproject)
  shopt -u nullglob
  ((${#project_matches[@]} == 1)) || die "$PROJECT 바로 아래에서 .uproject 하나를 찾을 수 없습니다."
  PROJECT=${project_matches[0]}
fi
[[ -f "$PROJECT" ]] || die ".uproject를 찾을 수 없습니다: $PROJECT"
[[ -d "$ENGINE_ROOT" ]] || die "Unreal Engine 루트를 찾을 수 없습니다: $ENGINE_ROOT"

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$PROJECT")" && pwd)
PROJECT="$PROJECT_DIR/$(basename -- "$PROJECT")"
ENGINE_ROOT=$(CDPATH= cd -- "$ENGINE_ROOT" && pwd)
fi
if [[ -z "$WORKSPACE_ROOT" ]]; then
  WORKSPACE_ROOT=$PROJECT_DIR
else
  [[ -d "$WORKSPACE_ROOT" ]] || die "워크스페이스를 찾을 수 없습니다: $WORKSPACE_ROOT"
  WORKSPACE_ROOT=$(CDPATH= cd -- "$WORKSPACE_ROOT" && pwd)
fi

HOST_OS=$(uname -s)
HOST_ARCH=$(uname -m)
if [[ "${INTEL_MAC_INSTALLER_TEST:-0}" != "1" ]]; then
  [[ "$HOST_OS" == "Darwin" && "$HOST_ARCH" == "x86_64" ]] || \
    die "이 설치기는 Intel Mac 전용입니다 (현재: $HOST_OS/$HOST_ARCH)."
fi

note "설치 계획"
echo "  Project:   $PROJECT"
echo "  Workspace: $WORKSPACE_ROOT"
echo "  Engine:    $ENGINE_ROOT"
echo "  VM:        $VM_NAME ($CPUS CPU, $MEMORY RAM, $DISK disk)"
echo "  Model:     $MODEL"
if ((DRY_RUN)); then
  if [[ "$ENGINE_KIND" == "unity" ]]; then
    echo "  Steps: Lima -> VM -> llmster/LM Link -> LOCAL Unity MCP/Bridge/worker -> local CLI -> explicit connection checks"
    exit 0
  fi
  echo "  Steps: Lima -> VM -> llmster/login -> LM Link -> MCP/RAG -> server -> checks"
  exit 0
fi

for command in curl tar shasum awk sed grep; do
  command -v "$command" >/dev/null 2>&1 || die "필수 명령을 찾을 수 없습니다: $command"
done

resolve_limactl() {
  if [[ -n "${LIMACTL_BIN:-}" && -x "$LIMACTL_BIN" ]]; then
    printf '%s\n' "$LIMACTL_BIN"
  elif command -v limactl >/dev/null 2>&1; then
    command -v limactl
  elif [[ -x "$HOME/.local/bin/limactl" ]]; then
    printf '%s\n' "$HOME/.local/bin/limactl"
  fi
}

LIMACTL=$(resolve_limactl || true)
if [[ -z "$LIMACTL" ]]; then
  if command -v brew >/dev/null 2>&1; then
    note "Homebrew로 Lima를 설치합니다."
    brew install lima
    LIMACTL=$(command -v limactl)
  else
    note "Lima $LIMA_VERSION Intel Mac 바이너리를 설치합니다."
    TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/lmstudio-intel.XXXXXX")
    ASSET="lima-${LIMA_VERSION}-Darwin-x86_64.tar.gz"
    BASE_URL="https://github.com/lima-vm/lima/releases/download/v${LIMA_VERSION}"
    curl -fL --retry 3 -o "$TEMP_DIR/$ASSET" "$BASE_URL/$ASSET"
    curl -fL --retry 3 -o "$TEMP_DIR/SHA256SUMS" "$BASE_URL/SHA256SUMS"
    EXPECTED=$(awk -v asset="$ASSET" '$2 == asset || $2 == "*" asset {print $1; exit}' "$TEMP_DIR/SHA256SUMS")
    [[ -n "$EXPECTED" ]] || die "Lima 체크섬을 찾을 수 없습니다."
    ACTUAL=$(shasum -a 256 "$TEMP_DIR/$ASSET" | awk '{print $1}')
    [[ "$ACTUAL" == "$EXPECTED" ]] || die "Lima 다운로드 SHA-256 검증에 실패했습니다."
    LIMA_PREFIX="$HOME/.local/lima-$LIMA_VERSION"
    mkdir -p "$LIMA_PREFIX" "$HOME/.local/bin"
    tar -xzf "$TEMP_DIR/$ASSET" -C "$LIMA_PREFIX"
    ln -sfn "$LIMA_PREFIX/bin/limactl" "$HOME/.local/bin/limactl"
    LIMACTL="$HOME/.local/bin/limactl"
  fi
fi
note "$($LIMACTL --version) 사용"

yaml_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

if ! "$LIMACTL" list -q "$VM_NAME" 2>/dev/null | grep -Fxq "$VM_NAME"; then
  TEMP_DIR=${TEMP_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/lmstudio-intel.XXXXXX")}
  VM_CONFIG="$TEMP_DIR/lima.yaml"
  PROJECT_YAML=$(yaml_escape "$WORKSPACE_ROOT")
  ENGINE_YAML=$(yaml_escape "$ENGINE_ROOT")
  if [[ "$ENGINE_KIND" == "unity" ]]; then
    MOUNTS_YAML="mounts: []"
  else
    MOUNTS_YAML=$(printf "mounts:\n  - location: '%s'\n    mountPoint: '%s'\n    writable: false\n  - location: '%s'\n    mountPoint: '%s'\n    writable: false" "$PROJECT_YAML" "$PROJECT_YAML" "$ENGINE_YAML" "$ENGINE_YAML")
  fi
  cat >"$VM_CONFIG" <<EOF
minimumLimaVersion: 2.0.0
arch: x86_64
vmType: vz
cpus: $CPUS
memory: $MEMORY
disk: $DISK
images:
  - location: https://cloud-images.ubuntu.com/releases/jammy/release/ubuntu-22.04-server-cloudimg-amd64.img
    arch: x86_64
$MOUNTS_YAML
containerd:
  system: false
  user: false
EOF
  note "x86_64 Ubuntu VM을 생성합니다: $VM_NAME"
  "$LIMACTL" create --name "$VM_NAME" --tty=false "$VM_CONFIG"
fi

note "VM을 시작합니다."
"$LIMACTL" start --tty=false "$VM_NAME"

if [[ "$ENGINE_KIND" == "unreal" ]] && ! "$LIMACTL" shell --tty=false "$VM_NAME" -- bash -lc \
  'test -r "$1" && test -d "$2"' bash "$PROJECT" "$ENGINE_ROOT"; then
  die "기존 VM에 프로젝트/엔진 마운트가 없습니다. 다른 --vm-name을 사용하거나 기존 VM 구성을 확인하세요."
fi

note "설치 저장소를 VM의 관리 경로로 동기화합니다."
if [[ "$ENGINE_KIND" == "unreal" ]]; then
LC_ALL=C COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='.DS_Store' \
  -C "$SCRIPT_DIR" -cf - . | \
  "$LIMACTL" shell --tty=false "$VM_NAME" -- bash -lc \
    'mkdir -p "$HOME/UE5_Local_LLM_MCP_lmstudio" && LC_ALL=C tar -xf - -C "$HOME/UE5_Local_LLM_MCP_lmstudio"'
fi

note "VM에 llmster를 설치하거나 기존 설치를 재사용합니다."
"$LIMACTL" shell --tty=false "$VM_NAME" -- bash -lc \
  'if [ ! -x "$HOME/.lmstudio/bin/lms" ]; then curl -fsSL https://lmstudio.ai/install.sh | bash; fi; "$HOME/.lmstudio/bin/lms" daemon up'

if ! "$LIMACTL" shell --tty=false "$VM_NAME" -- bash -lc \
  '"$HOME/.lmstudio/bin/lms" login --status >/dev/null 2>&1'; then
  if ((SKIP_LOGIN)); then
    die "LM Studio 로그인이 필요하지만 --skip-login이 지정됐습니다."
  fi
  note "LM Studio 계정 페어링을 시작합니다. 표시되는 URL을 브라우저에서 승인하세요."
  "$LIMACTL" shell --tty=true "$VM_NAME" -- bash -lc '"$HOME/.lmstudio/bin/lms" login'
fi

note "LM Link를 활성화하고 장치 이름을 설정합니다."
"$LIMACTL" shell --tty=false "$VM_NAME" -- bash -lc \
  '"$HOME/.lmstudio/bin/lms" link enable; "$HOME/.lmstudio/bin/lms" link set-device-name "$1"' \
  bash "$DEVICE_NAME"

guest_quote() {
  printf '%q' "$1"
}

INSTALL_COMMAND="cd \"\$HOME/UE5_Local_LLM_MCP_lmstudio\" && ./install.sh --profile standard --yes --headless-lmlink"
INSTALL_COMMAND+=" --active-project $(guest_quote "$PROJECT")"
INSTALL_COMMAND+=" --workspace-root $(guest_quote "$WORKSPACE_ROOT")"
INSTALL_COMMAND+=" --engine-root $(guest_quote "$ENGINE_ROOT")"
if ((BUILD_RAG)); then
  INSTALL_COMMAND+=" --build-rag --index-tier standard"
fi

note "MCP, 프로젝트 구성 및 RAG를 설치합니다."
if [[ "$ENGINE_KIND" == "unity" ]]; then
  UNITY_CONFIG="$WORKSPACE_ROOT/Library/EvidenceFirst/unity-mcp.json"
  unity_options=(--profile custom --components unity --yes --unity-project "$PROJECT" --unity-mcp-config "$UNITY_CONFIG")
  [[ -z "$UNITY_EDITOR" ]] || unity_options+=(--unity-editor "$UNITY_EDITOR")
  "$SCRIPT_DIR/install.sh" "${unity_options[@]}"
  IFS= read -r LOCAL_PYTHON < "$HOME/.evidence-first/runtime-python.path"
  [[ -x "$LOCAL_PYTHON" ]] || die "로컬 설치 Python 경로를 찾을 수 없습니다."
else
"$LIMACTL" shell --tty=false "$VM_NAME" -- bash -lc "$INSTALL_COMMAND"
fi

note "llmster 설정을 다시 불러오고 API 서버를 시작합니다."
"$LIMACTL" shell --tty=false "$VM_NAME" -- bash -lc \
  '"$HOME/.lmstudio/bin/lms" daemon down || true; sleep 1; "$HOME/.lmstudio/bin/lms" daemon up; sleep 2; "$HOME/.lmstudio/bin/lms" server start --port 1234'

LINK_OK=0
for _ in 1 2 3 4 5 6; do
  LINK_STATUS=$("$LIMACTL" shell --tty=false "$VM_NAME" -- bash -lc \
    '"$HOME/.lmstudio/bin/lms" link status' 2>&1 || true)
  if grep -q 'Status: Online' <<<"$LINK_STATUS" && grep -q 'Status: connected' <<<"$LINK_STATUS"; then
    LINK_OK=1
    break
  fi
  sleep 5
done
echo "$LINK_STATUS"
((LINK_OK)) || die "LM Link가 제한 시간 안에 연결되지 않았습니다. 상대 장치가 온라인인지 확인하세요."

note "등록된 MCP 서버를 실제로 초기화하고 도구 목록을 확인합니다."
if [[ "$ENGINE_KIND" == "unity" ]]; then
  "$LOCAL_PYTHON" "$SCRIPT_DIR/scripts/headless_mcp_chat.py" --mcp-config "$UNITY_CONFIG" --list-tools
  "$LOCAL_PYTHON" "$SCRIPT_DIR/scripts/headless_mcp_chat.py" --mcp-config "$UNITY_CONFIG" --model "$MODEL" --endpoint "$MODEL_ENDPOINT" --verify-install unity_status
else
MCP_TOOLS=$("$LIMACTL" shell --tty=false "$VM_NAME" -- bash -lc \
  'IFS= read -r runtime_python < "$HOME/.evidence-first/runtime-python.path"; test -x "$runtime_python" || exit 1; "$runtime_python" "$HOME/UE5_Local_LLM_MCP_lmstudio/scripts/headless_mcp_chat.py" --list-tools')
echo "$MCP_TOOLS"
for required in evidence-first unreal-rag unreal-agent; do
  grep -q "^${required}"$'\t' <<<"$MCP_TOOLS" || die "MCP 검증 실패: $required"
done

note "모델이 MCP 도구를 실제 호출하는지 확인합니다."
"$LIMACTL" shell --tty=false "$VM_NAME" -- bash -lc \
  'IFS= read -r runtime_python < "$HOME/.evidence-first/runtime-python.path"; test -x "$runtime_python" || exit 1; "$runtime_python" "$HOME/UE5_Local_LLM_MCP_lmstudio/scripts/headless_mcp_chat.py" --model "$1" --endpoint "$2" --verify-install unreal_rag_health' \
  bash "$MODEL" "$MODEL_ENDPOINT"
fi

note "프로젝트에 MCP-aware CLI 실행기를 설치합니다."
CLI_TARGET="$WORKSPACE_ROOT/lmstudio-cli.sh"
if [[ -f "$CLI_TARGET" ]] && ! cmp -s "$SCRIPT_DIR/scripts/lmstudio-headless-cli.sh" "$CLI_TARGET"; then
  CLI_BACKUP="$CLI_TARGET.backup-$(date '+%Y%m%d%H%M%S')"
  cp "$CLI_TARGET" "$CLI_BACKUP"
  note "기존 CLI 실행기를 보존했습니다: $CLI_BACKUP"
fi
cp "$SCRIPT_DIR/scripts/lmstudio-headless-cli.sh" "$CLI_TARGET"
chmod +x "$CLI_TARGET"
CLI_CONFIG="$WORKSPACE_ROOT/lmstudio-cli.conf"
if [[ -f "$CLI_CONFIG" ]]; then cp "$CLI_CONFIG" "$CLI_CONFIG.backup-$(date '+%Y%m%d%H%M%S')"; fi
printf 'VM=%s\nMODEL=%s\nLIMACTL=%s\nENGINE=%s\n' "$VM_NAME" "$MODEL" "$LIMACTL" "$ENGINE_KIND" > "$CLI_CONFIG"
printf 'ENDPOINT=%s\n' "$MODEL_ENDPOINT" >> "$CLI_CONFIG"
if [[ "$ENGINE_KIND" == "unity" ]]; then
  printf 'PYTHON=%s\nCLIENT=%s\nMCP_CONFIG=%s\n' "$LOCAL_PYTHON" "$SCRIPT_DIR/scripts/headless_mcp_chat.py" "$UNITY_CONFIG" >> "$CLI_CONFIG"
fi
chmod 600 "$CLI_CONFIG"

cat <<EOF

설치 완료
  CLI:     $WORKSPACE_ROOT/lmstudio-cli.sh
  Status:  cd $(guest_quote "$WORKSPACE_ROOT") && ./lmstudio-cli.sh status
  Chat:    cd $(guest_quote "$WORKSPACE_ROOT") && ./lmstudio-cli.sh
  Tools:   cd $(guest_quote "$WORKSPACE_ROOT") && ./lmstudio-cli.sh mcp-tools

참고: 기본 lms chat은 MCP 통합 옵션이 없으므로, 위 lmstudio-cli.sh가 등록된
MCP 도구를 모델에 전달하고 도구 요청을 자동 실행합니다.
EOF
