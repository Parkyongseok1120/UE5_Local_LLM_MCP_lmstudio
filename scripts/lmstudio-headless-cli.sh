#!/usr/bin/env bash

set -euo pipefail

CLI_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALL_VM="lmstudio-link"
INSTALL_MODEL="qwen/qwen3.8-27b"
INSTALL_LIMA="${HOME}/.local/bin/limactl"
INSTALL_ENGINE="unreal"
INSTALL_PYTHON=""
INSTALL_CLIENT=""
INSTALL_CONFIG=""
INSTALL_ENDPOINT="http://127.0.0.1:1234/v1/chat/completions"
# Data only: never source/eval this user-editable configuration.
if [[ -f "$CLI_DIR/lmstudio-cli.conf" ]]; then
  while IFS='=' read -r key value; do
    case "$key" in
      VM) INSTALL_VM=$value ;; MODEL) INSTALL_MODEL=$value ;; LIMACTL) INSTALL_LIMA=$value ;;
      ENGINE) INSTALL_ENGINE=$value ;; PYTHON) INSTALL_PYTHON=$value ;; CLIENT) INSTALL_CLIENT=$value ;; MCP_CONFIG) INSTALL_CONFIG=$value ;;
      ENDPOINT) INSTALL_ENDPOINT=$value ;;
    esac
  done < "$CLI_DIR/lmstudio-cli.conf"
fi
LIMACTL_BIN="${LIMACTL_BIN:-$INSTALL_LIMA}"
LMSTUDIO_VM="${LMSTUDIO_VM:-$INSTALL_VM}"
LMSTUDIO_MODEL="${LMSTUDIO_MODEL:-$INSTALL_MODEL}"
LMSTUDIO_CHAT_ENDPOINT="${LMSTUDIO_CHAT_ENDPOINT:-$INSTALL_ENDPOINT}"

if [[ ! -x "$LIMACTL_BIN" ]]; then
  LIMACTL_BIN="$(command -v limactl 2>/dev/null || true)"
fi
if [[ -z "$LIMACTL_BIN" || ! -x "$LIMACTL_BIN" ]]; then
  echo "오류: limactl을 찾을 수 없습니다." >&2
  exit 1
fi

run_lms() {
  "$LIMACTL_BIN" shell "$LMSTUDIO_VM" -- bash -lc \
    'exec "$HOME/.lmstudio/bin/lms" "$@"' bash "$@"
}

run_mcp_chat() {
  if [[ "$INSTALL_ENGINE" == "unity" ]]; then
    [[ -x "$INSTALL_PYTHON" && -f "$INSTALL_CLIENT" && -f "$INSTALL_CONFIG" ]] || { echo "Unity local MCP launcher configuration is incomplete" >&2; return 1; }
    "$INSTALL_PYTHON" "$INSTALL_CLIENT" --mcp-config "$INSTALL_CONFIG" --model "$LMSTUDIO_MODEL" --endpoint "$LMSTUDIO_CHAT_ENDPOINT" "$@"
    return
  fi
  "$LIMACTL_BIN" shell "$LMSTUDIO_VM" -- bash -lc \
    'IFS= read -r runtime_python < "$HOME/.evidence-first/runtime-python.path"; test -x "$runtime_python" || exit 1; exec "$runtime_python" "$HOME/UE5_Local_LLM_MCP_lmstudio/scripts/headless_mcp_chat.py" --model "$1" "${@:2}"' \
    bash "$LMSTUDIO_MODEL" --endpoint "$LMSTUDIO_CHAT_ENDPOINT" "$@"
}

show_compactor_status() {
  if [[ "$INSTALL_ENGINE" == "unity" ]]; then
    if [[ -f "$(dirname -- "$INSTALL_CLIENT")/headless_compact.js" ]]; then
      echo "Headless Compactor: available (local background-only)"
      return
    fi
    echo "Headless Compactor: unavailable (local)" >&2
    return 1
  fi
  if "$LIMACTL_BIN" shell --tty=false "$LMSTUDIO_VM" -- bash -lc \
    'test -f "$HOME/UE5_Local_LLM_MCP_lmstudio/scripts/headless_compact.js"'; then
    echo "Headless Compactor: enabled (background-only; 24 messages / 14K remaining-token threshold)"
  else
    echo "Headless Compactor: unavailable" >&2
    return 1
  fi
}

show_help() {
  cat <<'EOF'
사용법:
  ./lmstudio-cli.sh                  모든 MCP + 백그라운드 Compactor + 실시간 스트리밍 채팅
  ./lmstudio-cli.sh "질문 내용"      MCP 도구를 허용하고 스트리밍으로 한 번 질문
  ./lmstudio-cli.sh status           LM Link/서버/모델 상태 확인
  ./lmstudio-cli.sh models           설치된 모델 목록
  ./lmstudio-cli.sh mcp-tools        연결된 MCP 도구 목록
  ./lmstudio-cli.sh compactor-status 백그라운드 Compactor 상태
  ./lmstudio-cli.sh shell            LM Studio Linux VM 접속
  ./lmstudio-cli.sh server-start     API 서버 시작
  ./lmstudio-cli.sh server-stop      API 서버 중지
  ./lmstudio-cli.sh help             도움말

환경 변수:
  LMSTUDIO_MODEL   사용할 모델 (기본값: qwen/qwen3.8-27b)
  LMSTUDIO_VM      Lima VM 이름 (기본값: lmstudio-link)
  LIMACTL_BIN      limactl 실행 파일 경로
EOF
}

case "${1:-chat}" in
  help|-h|--help)
    show_help
    ;;
  status)
    run_lms daemon status
    run_lms link status
    run_lms server status
    run_lms ps
    show_compactor_status
    ;;
  models)
    run_lms ls
    ;;
  mcp-tools)
    run_mcp_chat --list-tools
    ;;
  compactor-status)
    show_compactor_status
    ;;
  shell)
    exec "$LIMACTL_BIN" shell "$LMSTUDIO_VM"
    ;;
  server-start)
    run_lms server start --port 1234
    ;;
  server-stop)
    run_lms server stop
    ;;
  chat)
    shift || true
    run_mcp_chat "$@"
    ;;
  *)
    run_mcp_chat "$*"
    ;;
esac
