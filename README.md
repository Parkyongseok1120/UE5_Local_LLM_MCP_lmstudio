
## UE5_Local_LLM_MCP_lmstudio

This is a **local RAG + MCP stack** for using local LLMs in LM Studio as Unreal Engine 5.x C++ assistants.

The old name was **Unreal58-RAG**, but I changed it so the LM Studio / MCP / Unreal structure is clearer.

For now, this has been officially tested on **UE 5.8**.  
Other UE 5.x versions are not impossible to use, but in that case I recommend building your own index from your local Unreal Engine source or project data.

Basically, it is meant to be used in a BYOI style.

> BYOI = Bring Your Own Index  
> Build your own RAG index from the Unreal Engine install, project code, and build logs on your own PC.

---

## What this is

If you just load a local model in LM Studio and ask it “teach me Unreal C++,” the model will answer based on what it already knows.

The problem is that Unreal has many versions, a large API surface, and lots of issues that depend on the actual project structure, such as `Build.cs` or include problems.  
So if you rely only on the model’s memory, it can hallucinate pretty easily or give vague answers.

This project creates a search index from Unreal documentation, source code, project code, and build logs, then injects relevant context when asking questions.

Roughly, the responsibilities are split like this.

```text
Unreal knowledge / API evidence = RAG
Answer tone / format / habits = LoRA
Actual workflow integration = MCP / Agent
```

So the goal is not to force all Unreal documentation into a LoRA.  
That would be painful to maintain, and once the Unreal version changes, it quickly becomes awkward.

I think it is much more realistic to first improve RAG search quality, then later use a small LoRA only for answer style and habits.

---

## Roughly, this is for

- Unreal C++ code assistance
- Explaining `UCLASS`, `UPROPERTY`, and `UFUNCTION`
- Checking `Build.cs` module dependencies
- Checking include issues
- Analyzing UHT / `generated.h` errors
- Finding fix directions from compile logs
- Building a local Unreal Agent setup with Rider / Cline / LM Studio
- Asking questions based on your own project code
- Adding game design documents to help with prototype planning

This is not trying to become some kind of fully automatic developer.  
The first goal is simply to make local models hallucinate less when dealing with Unreal C++.

---

## Recommended folder layout

For LM Studio, I recommend putting the two folders side by side like this.

```text
~/.lmstudio/
  Unreal58-RAG/
  lmstudio-unreal-agent-mcp/
```

You do not have to use it as a monorepo.  
You can clone both repos side by side and just point the MCP config to each root correctly.

---

## Quick install flow

```powershell
cd $HOME\.lmstudio\Unreal58-RAG
.\installer\INSTALL.bat
.\installer\Configure-Knowledge.ps1   # when available: builds the index from your UE install
.\rag.ps1 doctor
```

The basic flow is this.

1. Install LM Studio.
2. Load a local coding model.  
   Example: Qwen 3.6 27B.
3. Patch the MCP config.

```powershell
python scripts\patch_mcp_config.py
```

4. Apply the system prompt.

```text
prompts/lmstudio_unreal_agent_system.md
```

5. Enable the MCP servers in LM Studio.

```text
unreal-rag
unreal-agent
```

---

## Important notice

This project is not for redistributing Epic Games’ Unreal Engine source code or documentation.

**Do not commit pre-built `data/` indexes or Epic source exports.**

The related details are written here.

```text
EPIC_NOTICE.md
```

Before pushing publicly, I recommend running this check at least once.

```powershell
.\installer\Verify-Oss-Ready.ps1
```

This is not there just because I felt like adding another script.  
It is a safety check to prevent accidentally uploading Epic source files or generated indexes.

---

## Quick start

Even if the `python` command is not available in `PATH`, like on this PC, `rag.ps1` will automatically find and use the bundled Codex Python runtime.

```powershell
.\rag.ps1 collect-source
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 build
.\rag.ps1 query -Question "How do I create a UActorComponent in C++?"
```

After starting the LM Studio Local Server, you can ask questions like this.

```powershell
.\rag.ps1 lmstudio-models
.\rag.ps1 ask -Question "Show me a C++ example of attaching a custom Component to an Actor"
```

---

## Collecting local Unreal C++ source

If you have Unreal Engine 5.8 source locally, you can include it in the index.

```powershell
python scripts\collect_unreal_source.py `
  --root "C:\Program Files\Epic Games\UE_5.8\Engine\Source" `
  --out data\unreal58\raw_source.jsonl
```

If your source path is different, just change `--root`.

By default, the `ThirdParty` folder is excluded.  
If you want to include external libraries like Boost or WebRTC too, add this option.

```powershell
--include-third-party
```

You can also run it through the wrapper command.

```powershell
.\rag.ps1 collect-source -IncludeThirdParty
```

Personally, I recommend starting without ThirdParty first.  
If you put too much in from the beginning, the search quality can actually become less clear.

---

## Collecting local Unreal projects

By default, it searches for `.uproject` files under this path.

```text
$HOME\Documents\Unreal Projects
```

Run it like this.

```powershell
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 build
```

If you use another path, specify it directly.

```powershell
.\rag.ps1 collect-projects -ProjectsRoot "D:\MyUnrealProjects" -CopyProjectText
```

The text snapshot is generated here.

```text
data\unreal_projects\text_snapshot
```

This does not copy the entire project.  
It mainly collects C++ / Config / Plugin / text files needed for RAG.

So it is closer to a tool that extracts only the readable text for the model, not a backup tool.

---

## Extra collection for Unreal programming assistance

If you want better support for Unreal C++ code generation, compile error fixes, UHT/reflection issues, and Build.cs dependency advice, it is better to collect these extra data sources too.

```powershell
.\rag.ps1 collect-symbols
.\rag.ps1 collect-module-graph
.\rag.ps1 collect-project-profile -ProjectsRoot "C:\Path\To\YourProject"
.\rag.ps1 collect-build-logs -BuildLogRoot "C:\Path\To\YourProject" -LogsOnly
.\rag.ps1 build
```

The main collected data includes:

- `UCLASS`
- `USTRUCT`
- `UINTERFACE`
- `UENUM`
- `UFUNCTION`
- `UPROPERTY`
- include lists
- `*.Build.cs` module dependencies
- optional partial C++ function definitions

This part helps more directly with Unreal C++ assistance than plain document search.  
Especially for Build.cs, include, and generated.h issues, models often give vague answers unless they can see project-specific information.

---

## Search modes

The search modes are separated for Unreal programming assistance.

| Mode | Purpose |
|---|---|
| `codegen` | Search evidence for writing new Unreal C++ code |
| `compile_fix` | Fix compile / link errors |
| `runtime_debug` | Analyze Editor logs, crashes, asserts, and ensures |
| `api_lookup` | Check Unreal API, signatures, and includes |
| `module_fix` | Fix Build.cs, include, and module dependency issues |
| `reflection_fix` | Fix UHT, generated.h, and reflection macro issues |

For example, `compile_fix` does not just pull random documents.  
It assembles context in this order: build log → symbol → module/include → project profile → fix playbook.

Basically, it tries to bring the most relevant material for the current situation to the front.

---

## module graph

`collect-module-graph` builds a module dependency graph from the collected symbols and include information.

For example, it adds evidence for checking things like:

- which module owns `GameplayTagContainer.h`
- whether `PublicDependencyModuleNames` is needed when used in a public header
- whether `PrivateDependencyModuleNames` is enough when used only in a private `.cpp`
- where the include owner is

The generated summary report is here.

```text
Reports/unreal_module_include_graph.md
```

I added this because it is a surprisingly common place to get stuck in Unreal C++.  
A lot of times, the code itself looks correct, but the build breaks because Build.cs is wrong.

---

## project profile

`collect-project-profile` summarizes project-specific settings and adds them to RAG.

It roughly collects these files and settings.

- `.uproject`
- `.uplugin`
- `*.Build.cs`
- `*.Target.cs`
- `Config/*.ini`
- module list
- plugin list

Once you have an actual project, you can collect it again by specifying `-ProjectFile` or `-ProjectsRoot`.

```powershell
.\rag.ps1 collect-project-profile -ProjectFile "C:\Path\To\YourProject\YourProject.uproject"
```

With this included, the model can answer while seeing some of your actual module structure, instead of only giving generic Unreal answers.

---

## Regression tests

Basic regression test commands:

```powershell
.\rag.ps1 eval-unreal-programming
.\rag.ps1 eval-unreal-review -Answers tests\fixtures\unreal_review_answers\good_answers.jsonl
.\rag.ps1 test-build-logs
.\rag.ps1 test-unreal-readiness
```

`test-build-logs` uses synthetic Unreal log fixtures to check whether these errors are correctly split into `build_log` JSONL records.

- `C1083`
- `LNK2019`
- `UnrealHeaderTool`
- `generated.h` related errors

`eval-unreal-review` scores model answer JSON / JSONL files against Unreal C++ review E2E cases.

To print only the prompts, use this command.

```powershell
.\rag.ps1 eval-unreal-review -PrintPrompts
```

`test-unreal-readiness` checks whether the wrapper static validation catches these risks.

- `generated.h`
- reflection namespace
- `BlueprintNativeEvent`
- editor-only include
- UObject lifetime risks

It may look like there are a lot of tests, but with Unreal, one tiny mistake can break the whole build.  
So I think having these checks is the better direction.

---

## Extra operation commands

```powershell
.\rag.ps1 doctor
.\rag.ps1 bench-mcp
.\rag.ps1 eval-debug
.\rag.ps1 eval-genre
.\rag.ps1 eval-e2e-compile
.\rag.ps1 knowledge-audit
.\rag.ps1 agent-session -Question "TPS shooter prototype component"
.\rag.ps1 scaffold-prototype -ScaffoldGenre shooter
.\installer\Install-ClineUnrealMcp.ps1
```

---

## Usage modes

| Mode | Surface | Flow |
|---|---|---|
| Q&A | LM Studio chat | `unreal_rag_search` only |
| Agent | Cline + MCP, build/debug in Rider | `unreal_agent_session` → write → Rider Build |
| Compile loop | LM Studio / Cline | `unreal_start_compile_loop` |
| Runtime debug | LM Studio + MCP | `read_unreal_logs` → `runtime_debug` RAG |

For LM Studio setup, see this document.

```text
docs/LMStudio_Unreal_Agent_Setup.md
```

For Rider + Cline setup, I recommend this document.

```text
docs/Cline_Rider_Unreal_Agent_Setup.md
```

---

## VSCode Unreal C++ setup

This is optional.

If you want to set up C++ IntelliSense and debugging settings for a UE 5.8 project in VSCode, use this script.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_vscode_unreal.ps1 `
  -ProjectFile "C:\Path\To\MyGame.uproject"
```

You can also apply it to multiple project roots at once.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_vscode_unreal.ps1 `
  -ProjectsRoot "$HOME\Documents\Github"
```

This script roughly configures:

- global VSCode C++ defaults
- Unreal Editor Source Code Editor setting
- UBT project file format
- per-project `.vscode/settings.json`
- `tasks.json`
- `launch.json`
- `extensions.json`
- `compileCommands_*.json`

However, Blueprint-only projects without a `Source` folder are skipped because they are not C++ IntelliSense targets.

---

## UBT feedback loop

Once you have an actual project, the UnrealBuildTool-based feedback loop can be run like this.

```powershell
.\rag.ps1 ubt-feedback `
  -ProjectFile "C:\Path\To\YourProject\YourProject.uproject" `
  -UbtTarget "YourProjectEditor" `
  -Mode compile_fix `
  -Question "Find the cause of the latest build failure and suggest the minimum fix"
```

The goal is not just to explain what the error means.  
The goal is to find the minimum fix based on the build log.

In Unreal, it is often hard to fix things by looking at only one error message, so this is designed to also use the project profile, Build.cs, and include information when possible.

---

## Collecting game design documents

Project-specific game design documents should go under this path.

```text
Game_Design_Docs\<ProjectName>
```

An example is included here.

```text
Game_Design_Docs\_TEMPLATE
```

Folders starting with `_` are excluded from the default collection.

```powershell
.\rag.ps1 collect-game-design
.\rag.ps1 build
.\rag.ps1 query -Mode planning -Source game_design_doc -Project "MyGame" -Question "Summarize the core loop and first implementation unit"
```

You can optionally add front matter at the top of a document.

```markdown
---
title: Core Loop
genre: action_combat
design_area: core_loop
---
```

These values are reflected in search filters.

You can check game design search quality with this regression test.

```powershell
.\rag.ps1 eval-game-design
```

It checks whether the expected document appears in the top 5 results for each query.

This feature is meant to let the model look not only at Unreal API information, but also at what kind of game the project is supposed to be.

---

## Optional: collecting official documentation

Epic’s documentation site can sometimes return only titles instead of full body text because of SPA behavior and security checks.

So the more stable starting point is to index your local Unreal Engine source and project code first, rather than relying on official web documentation crawling.

Still, if you want to try collecting official documentation, you can run this.

```powershell
python scripts\collect_unreal_docs.py `
  --seeds config\unreal_57_seed_urls.txt `
  --out data\unreal58\raw_docs.jsonl `
  --max-pages 200 `
  --delay 0.5
```

Do not start with a huge crawl.  
I recommend testing with a small number first.

```powershell
--max-pages 20
```

Start around there, then increase it if it looks fine.

---

## Building the RAG index

```powershell
python scripts\build_rag_index.py `
  --input data\unreal58\raw_docs.jsonl data\unreal58\raw_source.jsonl `
  --out-dir data\unreal58
```

The generated files are:

```text
data\unreal58\chunks.jsonl
data\unreal58\rag.sqlite
```

If `raw_source.jsonl` does not exist yet, you can build with only `raw_docs.jsonl`.

---

## Search test

```powershell
python scripts\query_rag.py `
  --index data\unreal58\rag.sqlite `
  "What macros and Build.cs settings are needed when creating a UActorComponent in C++?"
```

---

## Connecting to LM Studio

Load the model you want in LM Studio, start the Local Server, then run this.

If `-Model` is omitted, the first model loaded in LM Studio is used automatically.

```powershell
python scripts\query_rag.py `
  --index data\unreal58\rag.sqlite `
  --ask-lmstudio `
  "Show me a C++ example of attaching a custom Component to an Actor in UE 5.8"
```

If you want to specify a model directly, use this.

```powershell
--model "MODEL_ID"
```

Or run it through the wrapper command.

```powershell
.\rag.ps1 ask -Model "MODEL_ID" -Question "your question"
```

You can check the model IDs currently detected by LM Studio with this command.

```powershell
.\rag.ps1 lmstudio-models
```

---

## Using MCP in the LM Studio app chat

LM Studio can connect MCP servers to the app chat.  
This project provides the following MCP tool.

```text
unreal_rag_search
```

The setup flow is:

1. Open the `Program` tab in the right sidebar of LM Studio.
2. Click `Install > Edit mcp.json`.
3. Add the `mcpServers` contents from this template to LM Studio’s `mcp.json`.

```text
config/cline_mcp_settings.template.json
```

4. Restart LM Studio or refresh the MCP list.
5. When asking Unreal-related questions in chat, say something like this.

```text
Use unreal_rag_search first if needed.
```

---

## mcp.json BOM error

LM Studio may show this error.

```text
Unexpected token '﻿'
```

This is likely caused by `mcp.json` being saved as UTF-8 with BOM.  
You can rewrite the config as UTF-8 without BOM using this command.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_lmstudio_mcp.ps1
```

---

## Workspace name

The old folder name `Gemma4 LORA` does not really fit the current structure, so I unified it as **Unreal58-RAG**.

The reason is simple.

- It matches the MCP server name `unreal-rag`
- It does not make LoRA look like the main feature
- The real core is RAG + MCP + Unreal assistance

If the physical folder is still named `Gemma4 LORA`, either create a junction named `Unreal58-RAG`, or close LM Studio / Cursor and rename the folder.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rename_workspace_and_refresh_lmstudio.ps1
```

---

## Workspace folder rename

This is optional.

The script handles these tasks after renaming the folder.

- Rebuilds the RAG index
- Updates the LM Studio `mcp.json` path
- Updates internal sync file paths
- Checks that files are saved without UTF-8 BOM

The tool name remains:

```text
unreal_rag_search
```

---

## When should LoRA be used?

Personally, I think it is better to improve RAG search quality first.

LoRA should not be used from the beginning as a way to store Unreal knowledge.  
It is better to start with small goals like these.

- Fixing the Unreal C++ answer format
- Making the model consistently explain `UCLASS`, `UPROPERTY`, `UFUNCTION`, and `Build.cs`
- Making the model search for evidence instead of inventing unknown APIs
- Fixing Korean explanation + C++ example code style
- Making the model avoid skipping risky code or vague include/module dependency issues

Trying to put everything into LoRA from the start is too hard to maintain.  
Unreal has version differences, a huge API surface, and project-specific Build.cs structures, so memorizing everything is not as useful as it sounds.

So this project starts by improving local RAG quality first,  
then adds LoRA only as much as needed later.

---

## Summary

The goal of this project is not to build some grand “fully automatic developer.”

For now, the goal is to make local models better at answering Unreal Engine C++ questions by:

- reducing hallucinations
- searching for evidence first
- handling Build.cs / include / reflection issues better
- connecting into a real workflow inside Rider / Cline / LM Studio

This project is not perfect yet.  
It is still experimental, and the structure may change.

Still, I think it can be a starting point for people who want to use local LLMs with Unreal C++.
````
