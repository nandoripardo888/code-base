# Experimental image card

The `ShowImage` MCP tool requests an image-first card: rounded corners, a small
caption, theme adaptation, and a host fullscreen request when clicked. Multiple
images become a swipeable carousel with arrows, dots, and a position counter.
Inline carousel cards are 200 × 240 px (180 × 220 px on narrow screens), with
compact captions and a single navigation row. The iframe reports its content
height to the host. Single-image previews keep their original dimensions.
The selected image is retained when entering and leaving fullscreen. It does not create
a native ChatGPT attachment. The host controls outer chrome and resource support.

## Run the real test

1. Restart the code-harness MCP process with the normal launcher after installing
   the updated package (`python -m pip install -e .`). Use the same tunnel/profile.
2. Refresh the connector's tool definitions in ChatGPT, if needed, and open a new
   conversation with that connector. Check that `ShowImage` is available.
3. Use this prompt on this workstation (the existing `mycut` project alias is required):

```text
Use ShowImage do code-base com estes argumentos:
{
  "project": "mycut",
  "path": "output/playwright/wave0-barber-390x844.png",
  "title": "MyCut · Home do barbeiro"
}
Não substitua por Read nem por um link. Depois da chamada, pergunte se eu vejo
o card. Não afirme que a imagem apareceu só porque a ferramenta funcionou.
```

This uses an existing local capture as a visual fixture, not evidence of the
current MyCut UI. For another machine, choose any PNG/JPEG/GIF/WebP under a
configured project. Limits: 8 images, 4 MiB each, 8 MiB combined. No public URL is needed.

For a carousel, use an array in `path` (one item still produces the single card):

```json
{
  "project": "mycut",
  "path": [
    "output/playwright/wave0-barber-390x844.png",
    "output/playwright/wave0-owner-390x844.png",
    "output/playwright/wave0-client-390x844.png"
  ],
  "title": "MyCut · Telas",
  "titles": ["Barbeiro", "Proprietário", "Cliente"]
}
```

`titles` is optional and must contain one caption per image. Files are returned in
the supplied order; a failed file aborts the whole batch without partial output.

The original single card was confirmed visible by the user in ChatGPT. Fullscreen
and the compact carousel require a new live check. The v3 URI avoids cached older HTML.

## Interpret the result

- Tool missing: running process or connector tool discovery is stale, or an
  explicit tool allowlist excludes `ShowImage`.
- Tool succeeds but no card: check whether this connector/tunnel/client supports
  `resources/read` and preserves tool/resource `_meta`; do not claim rendering.
- Card appears but image fails: inspect delivery of tool-result `_meta.imageCard`
  and whether the host permits `data:image/...` in its image CSP.
- Image appears: ask the user to confirm appearance and enlargement on the target
  device. Local browser simulation cannot establish ChatGPT mobile compatibility.

## Implementation

- Standard resource: `ui://code-harness/image-card-v3.html` with
  `text/html;profile=mcp-app`, linked by `_meta.ui.resourceUri`.
- Compatibility alias: `openai/outputTemplate` plus the legacy `window.openai`
  result-metadata bridge.
- Image bytes are in tool-result `_meta`, avoiding base64 in the model's text
  context. Use `Read` separately for model vision.
- UI resource is static and contains no project data. Image reads retain the
  project guard, allowlist and `code.read` OAuth scope. There is no public route,
  global image cache, network fetch, or executable SVG support.
- `prefersBorder: false` is only a preference; host framing may still appear.
- The UI advertises `inline` and `fullscreen`, requests `ui/request-display-mode`
  when supported, and also supports `window.openai.requestDisplayMode({mode})`.
  It follows the host's returned display mode and context updates. A denied or
  unavailable fullscreen request falls back to the in-card dialog, explicitly
  labeled as such. `Voltar` requests `inline`; it does not close the whole app.

## Local visual preview

```powershell
.venv\Scripts\python.exe scripts/preview-image-card.py --project C:\path\to\project --image screen.png --title "Screen preview"
```

Open the generated `.code-harness/image-card-preview/index.html`. It simulates the
MCP Apps handshake and tool-result delivery inside a sandboxed iframe. The preview
contains the selected image, stays local, and is ignored by Git. It is not a
ChatGPT rendering test.

Repeat `--image` for a carousel, and optionally repeat `--caption` with one title
per image. `--bridge legacy`, `--bridge denied`, and `--bridge unsupported` simulate
the compatibility bridge and hosts that decline or lack fullscreen. The default
mock host implements standard fullscreen and return to inline.

References:
- https://developers.openai.com/plugins/build/chatgpt-ui
- https://developers.openai.com/plugins/reference
- https://modelcontextprotocol.io/extensions/apps/overview
