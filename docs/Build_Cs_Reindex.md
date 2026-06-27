# After Phase 1 (Build.cs parser): re-run symbol + module graph collection, then incremental index build.
#   .\rag.ps1 collect-symbols --tier public
#   .\rag.ps1 collect-module-graph
#   .\rag.ps1 build-incremental
