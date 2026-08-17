# Built-in CPU AI

YouTube Media Studio 3 provides a private, zero-configuration AI provider for people
who do not have Ollama, a GPU, an API key, or AI-development experience. It is the
default provider on a new installation. AI remains optional per workspace.

## What is included

The application manages two pinned components in the current user's application-data
folder:

- **Qwen3-0.6B-Q8_0**, a compact multilingual Apache-2.0 language model used for
  structured planning, semantic comparison, ranking, and short explanations;
- **llama.cpp `b10453`**, an MIT-licensed CPU inference server bound only to
  `127.0.0.1` on a random free port.

The model is not hidden inside the main installer. On the first AI-enabled request,
the app downloads the platform runtime (about 11–18 MB) and model (about 610 MiB),
checks their exact size and SHA-256 digest, installs them atomically, then starts the
server. This keeps normal upgrades smaller while still requiring no model choice or
manual AI setup. **Global Settings → AI providers and online evidence → Built-in AI
assets** can install, repair, or remove the managed assets at any time.

An internet connection is required for the first built-in-AI use and for a repair
after removal. Once installed, model inference works offline. Workflows that explicitly
use YouTube, Wikipedia, catalogs, or web evidence still require internet.

## System requirements

These requirements are for the desktop application with built-in AI enabled. Media
encoding and very large libraries can benefit from more resources.

| | Minimum | Recommended |
| --- | --- | --- |
| Operating system | Windows 10/11 x64; 64-bit desktop Linux x64; or macOS 13+ on Apple silicon | Current Windows 11, current 64-bit Linux, or current macOS on Apple silicon |
| CPU | 64-bit CPU supported by the matching release; 2 physical cores | 4+ modern physical cores with AVX2 on x64, or Apple silicon |
| Memory | 8 GB RAM | 16 GB RAM or more |
| Free storage | 2 GB beyond the application and media library | 4 GB on an SSD for model, runtime, temporary downloads, and cache headroom |
| GPU / CUDA | Not required | Optional only when the user chooses an external Ollama or hosted/GPU setup |
| Network | Required for installation, first model download, YouTube, and online evidence | Broadband for the first approximately 630 MB AI download |

The CPU runtime deliberately reserves processing capacity for playback and the UI and
uses no GPU layers. A machine meeting only the minimum may take tens of seconds for a
complex Curator request because that workflow performs several independently checked
structured decisions. “CPU compatible” does not mean every historical processor or
32-bit operating system is supported; unsupported platforms can use a hosted provider.

The Raspberry Pi release is CLI-only and does not promise the built-in desktop AI
experience. Advanced users can still configure an external compatible endpoint.

## Provider order

The selected Global Settings provider is authoritative:

1. **Built-in CPU AI** starts the managed local model directly.
2. **Ollama** uses the explicitly selected Ollama model, then falls back to built-in AI
   if Ollama is unavailable.
3. **Hosted or custom provider** uses that provider first, then an explicitly configured
   Ollama model, then built-in AI.
4. If every model is unavailable, each workflow keeps its existing deterministic or
   evidence-only fallback and records the limitation in Live Logs.

Selecting a hosted provider is an explicit decision to send that task's bounded prompt
to the provider. Merely having an old API key saved does not override a selected local
provider.

## Per-user local preference profile

Smart Library Curator derives a preference profile for **whoever is currently using
the software**. It is not a profile of the developer and it is never baked into release
model weights.

The profile is rebuilt when Curator runs from:

- user-created playlist names;
- the title, artist, album, and year of playlist members;
- playlist membership as a positive user-curated signal.

It is stored as `ai/preference-profile.json` below the user's configured application
data folder. The file is versioned and contains no media paths. A fingerprint prevents
unnecessary rewrites. Renaming a playlist, changing its members, or deleting it changes
the next profile. The app bounds profile and prompt sizes so a huge library cannot
create an unbounded inference request.

There are no hardcoded mappings such as `fast hindi` → `hindi dance`. The selected
model judges whether the meaning of a playlist and its tracks supports the complete
request. Public catalog/web evidence remains an independent gate for facts such as
language, genre, tempo, or mood; a preference signal cannot invent track facts.

This is retrieval-time personalization, not continuous model-weight training. That
choice makes learning explainable, reversible, portable between provider choices, and
safe from one person's taste leaking into another person's release.

## AI switch behavior

- When a workspace's **Use AI for this task** switch is on, its model-assisted behavior
  is available through the selected provider and fallback chain.
- When Smart Library Curator's AI switch is off, its main action intentionally changes
  to **Search internet** and opens YouTube search. Version 3 does not change that
  behavior or pretend local semantic curation is active.
- Downloading, playback, editing, splitting, and deterministic search do not require AI.

## Security and privacy

- Runtime and model URLs, byte sizes, versions, and SHA-256 digests are pinned in code.
- Downloads use a temporary `.part` file and become active only after verification.
- Runtime archives are rejected if they contain path traversal or links.
- The managed server listens on loopback only and uses a random port.
- The model is launched with zero GPU layers and a bounded 8,192-token context.
- API keys remain in local settings and are redacted from diagnostic errors.
- Removing built-in assets does not remove playlists, settings, or the preference
  profile. The profile can be deleted manually from the documented data folder.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| First request remains on “AI working” | The initial model download is large. Use **Install / repair** in Global Settings to see installation state and retry safely. |
| Checksum or truncated-download error | Check free space and network filtering, then select **Install / repair**. A rejected `.part` file is removed automatically. |
| Unsupported platform message | Select Ollama, a hosted provider, or a custom OpenAI-compatible endpoint. |
| CPU inference is too slow | Reduce requested result count, close CPU-heavy programs, or choose a faster Ollama/hosted model. |
| Model should no longer occupy disk | Select **Remove model**. It downloads again automatically if built-in fallback is needed later. |
