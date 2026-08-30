# Third-Party Notices

Codex Window Sentinel's earlier interactive trigger strategy and bounded
quiet-period shutdown behavior were adapted from CCLimitPing by wavever. That
code was removed on 2026-08-30 when the trigger moved to the Codex app-server
protocol (see `docs/TUI_TRIGGER_POSTMORTEM.md`). The weekly-protection threshold
and the general idea of triggering a window with one minimal request through the
vendor's own CLI remain influenced by that project, so the notice stands:

https://github.com/wavever/CCLimitPing

CCLimitPing is licensed under the MIT License:

```text
MIT License

Copyright (c) 2026 wavever

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
