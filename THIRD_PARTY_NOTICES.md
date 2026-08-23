# Third-party runtime notices

Desktop packages of YouTube Media Studio include these command-line runtimes so media workflows can run without separate installation:

- **FFmpeg and FFprobe** are invoked as separate processes. Windows and macOS packages use the 7.1 builds obtained by `portable-ffmpeg` from BtbN FFmpeg Builds and OSXExperts. Linux x64 packages use the checksum-pinned, self-contained 6.0.1 static build from John Van Sickle. The selected builds are distributed under GNU GPL v3; FFmpeg source and licensing information are available from <https://ffmpeg.org/>, and the BtbN build scripts are available from <https://github.com/BtbN/FFmpeg-Builds>.
- **Deno** is invoked as a separate process by yt-dlp for JavaScript challenge support. Deno is MIT licensed and its source is available from <https://github.com/denoland/deno>.
- **yt-dlp and yt-dlp-ejs** provide extraction and JavaScript challenge components. Their source and applicable notices are available from <https://github.com/yt-dlp/yt-dlp> and <https://github.com/yt-dlp/ejs>.

The Python application and all other dependencies retain their respective licenses. This notice does not change the license of YouTube Media Studio.

The Python dependency bundle also includes **Agno** for model/provider orchestration,
the provider SDKs used by its Ollama, OpenAI-compatible, NVIDIA, and Anthropic adapters,
and **DDGS** for bounded public web evidence used by Smart Library Curator. These
libraries retain their upstream licenses and notices; their inclusion does not grant
access to any hosted service or bundle any provider API key.
