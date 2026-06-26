## UE5_Local_LLM_MCP_lmstudio

This is a **local RAG + MCP stack** for using local LLMs in LM Studio as Unreal Engine 5.x C++ assistants.

The old name was **Unreal58-RAG**, but I renamed it so the LM Studio / MCP / Unreal structure is clearer.

For now, this has been tested on **UE 5.8**.  
Other UE 5.x versions should also be usable, but I recommend building your own index from your local Unreal Engine install and project data.

In short, this is meant to be used in a BYOI style.

> BYOI = Bring Your Own Index  
> Build your own RAG index from the Unreal Engine install, project code, and build logs on your own PC.

---

## What this is

If you just load a local model in LM Studio and ask it about Unreal C++, the model answers based on what it already knows.

The problem is that Unreal has many versions, a large API surface, and lots of issues that depend on the actual project structure, such as `Build.cs`, include paths, modules, reflection macros, and generated headers.

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

## What this is for

This project is mainly for:

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

The basic flow is:

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

See:

```text
EPIC_NOTICE.md
```

Before pushing publicly, I recommend running this check at least once.

```powershell
.\installer\Verify-Oss-Ready.ps1
```

This is not just another random script.

It is a safety check to prevent accidentally uploading Epic source files or generated indexes.

---

## Quick start

Even if the `python` command is not available in `PATH`, `rag.ps1` will try to find and use the bundled Codex Python runtime.

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
