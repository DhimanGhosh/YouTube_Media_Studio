# Privacy Policy

**Effective date:** August 11, 2026, upon publication in the first release containing
this policy

This Privacy Policy explains how **YouTube Media Studio** (the "Application"),
maintained under the **DhimanTools** project identity, handles information when you use
the desktop application or command-line tools.

## Privacy at a glance

- The Application does not require an account with DhimanTools.
- DhimanTools does not operate an analytics, advertising, telemetry, or data-collection
  server for the Application.
- Settings, workspace state, media-library indexes, operation history, and optional
  diagnostic reports are stored locally on your device.
- Network access occurs only when a feature needs an online media, metadata, search, or
  AI service. Those requests go directly from your device to the relevant third party.
- The Application does not sell personal information.

**Network-transfer statement:** This program will not transfer any information to other
networked systems unless specifically requested by the user or the person installing or
operating it.

## Information stored locally

Depending on the features you use, the Application may store the following information
in its application-data folder:

- application preferences, window state, selected folders, and workspace forms;
- paths, filenames, tags, artwork references, and an index of media in folders you add;
- operation history, completion state, and estimated-duration profiles;
- provider selections, model names, custom provider URLs, and API keys;
- downloaded media, generated metadata, reports, caches, and temporary working files;
- optional diagnostic reports containing application events, platform and runtime
  details, command-line arguments, working-directory paths, exception messages, and
  thread stack traces.

API-key fields are masked in the interface, but saved keys are stored in the local
application settings file and are not guaranteed to be encrypted by the Application.
Protect your operating-system account and application-data folder, and use restricted,
revocable API keys where providers support them.

The Application does not automatically transmit locally stored information to
DhimanTools. Diagnostic reports are disabled by default, remain on the device, and are
shared only if you choose to provide them. In the current release, the Application
retains up to twelve recent diagnostic reports and removes older reports automatically.

## Information sent to third-party services

The Application connects directly to third-party services only as needed for features
you invoke. The information sent depends on the feature and may include:

- media URLs, search terms, requested formats, timestamps, and download parameters;
- track titles, artists, albums, release years, filenames, existing tags, and other
  metadata used for lookup, enrichment, or verification;
- artwork searches and URLs;
- natural-language requests, structured task instructions, bounded catalog or web
  evidence, and derived media metadata sent to a configured AI provider;
- API keys or other credentials required to authenticate directly with the service;
- standard network information, such as your IP address and request headers, which the
  receiving service normally obtains when your device connects to it.

Raw media files are not intentionally uploaded to AI providers by the Application's
text-based agent workflows. A custom provider or a future provider-specific capability
may behave according to its own configuration and policy, so review the destination
before enabling it.

The Application can interact with services in the following categories. The examples
reflect integrations available on the effective date and may change as providers are
added, removed, or renamed in later releases. Material changes will be reflected in this
policy; the current hosted-AI definitions are also visible in the public
[provider registry](src/youtube_audio_video_downloader/services/ai_provider_registry.py).

| Category | Examples | Relevant privacy information |
| --- | --- | --- |
| Media discovery and retrieval | YouTube and other sites supported by yt-dlp | [Google Privacy Policy](https://policies.google.com/privacy) and the policy of the selected site |
| Catalog and reference metadata | Apple/iTunes Search API, Wikipedia, and Wikimedia | [Apple Privacy Policy](https://www.apple.com/legal/privacy/), [Wikimedia Privacy Policy](https://foundation.wikimedia.org/wiki/Policy:Privacy_policy) |
| Web and image search | DuckDuckGo, Google Search, and optional SerpApi | [DuckDuckGo Privacy Policy](https://duckduckgo.com/privacy), [Google Privacy Policy](https://policies.google.com/privacy), [SerpApi Legal](https://serpapi.com/legal) |
| Hosted AI providers | NVIDIA NIM, OpenAI, Anthropic, Google Gemini, Groq, Hugging Face Inference, OpenRouter, OpenCode Zen, or a custom OpenAI-compatible endpoint | The privacy policy and data controls published by the provider you configure |
| Local AI | Ollama or another loopback/local endpoint | Model requests normally remain on the configured local endpoint; independent evidence searches can still access the internet |

Third-party services process information under their own privacy policies, terms,
retention rules, and account settings. DhimanTools does not control those services. You
are responsible for choosing providers, configuring their privacy controls, and
ensuring that you have permission to submit the relevant URLs, metadata, and content.

## Credentials

Provider credentials are sent only to the endpoint associated with the provider you
select, or to a custom endpoint you enter. The Application attempts to omit credentials
from its logs and error messages. It does not send saved credentials to DhimanTools.

You can clear a saved provider key in Global Settings and save the blank value. You
should also revoke compromised or unused keys through the provider's own account tools.

## Data retention and deletion

Local information remains on your device until it is replaced, reset, or deleted. You
can inspect the active application-data folder from Global Settings.

To remove local information, you can:

1. clear individual fields or saved provider credentials in Global Settings;
2. use **Reset app** to restore defaults and clear supported workspace state;
3. delete files from the application-data folder; or
4. choose **Also remove settings, history, and application data** when uninstalling.

Downloaded or edited media stored outside the application-data folder must be removed
separately. Information already sent to a third-party service is governed by that
service's retention and deletion controls.

## Security

The Application uses local storage and direct connections to configured services, but
no software or storage method is completely secure. Download releases only from the
official GitHub repository, verify published checksums where appropriate, protect your
credentials, and keep the Application and bundled tools updated.

## Children's privacy

The Application is a general-purpose media utility and is not directed to children.
DhimanTools does not knowingly collect children's personal information through an
Application-operated service.

## Changes to this policy

This policy may change when Application features, providers, or legal requirements
change. Updates will be committed to the public repository, and the effective date
above will be revised for material changes.

## Contact

For privacy questions or requests concerning the Application, open a GitHub issue at:

<https://github.com/DhimanGhosh/YouTube_Media_Studio/issues>

Do not include API keys, private media, sensitive filesystem paths, or other secrets in
a public issue. GitHub processes information submitted through its platform under the
[GitHub Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement).
