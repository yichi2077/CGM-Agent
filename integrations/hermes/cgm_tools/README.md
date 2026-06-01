# Hermes CGM Tools Spike

This is a minimal Hermes user-plugin wrapper for the local `hermes-cgm-agent` tool executor.

It is intentionally thin:

- Hermes owns the shell and tool-call surface.
- `hermes-cgm-agent` owns CGM domain logic, storage, reports and audit.
- The plugin calls `python -m hermes_cgm_agent tool-call reports.generate`.
- No files are copied into the Hermes installation tree by this project.

Manual installation should copy this folder into the user's Hermes plugin directory, for example:

```powershell
$env:CGM_AGENT_PROJECT_ROOT='C:\Users\postgres\Desktop\新建文件夹 (4)\hermes-cgm-agent'
```

Then enable the user plugin through the normal Hermes plugin configuration path.

