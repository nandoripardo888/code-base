"""Build a local mock-host preview; never serves or publishes the selected image."""

from __future__ import annotations

import argparse
import html
import json
from importlib.resources import files
from pathlib import Path

from code_harness.mcp.image_card import image_card
from code_harness.paths import PathGuard

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--project", type=Path, required=True)
parser.add_argument("--image", required=True, action="append")
parser.add_argument("--title", default="Prévia de imagem")
parser.add_argument("--caption", action="append")
parser.add_argument(
    "--bridge", choices=["standard", "legacy", "denied", "unsupported"], default="standard"
)
args = parser.parse_args()
result = image_card(PathGuard(args.project), args.image, args.title, args.caption)
payload = json.dumps(result.model_dump(by_alias=True), ensure_ascii=True).replace("<", "\\u003c")
component = files("code_harness.mcp").joinpath("image_card.html").read_text(encoding="utf-8")
component = component.replace(
    '<meta charset="utf-8">',
    '<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
    "content=\"default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data:; connect-src 'none'\">",
)
if args.bridge == "legacy":
    shim = """<script>
window.openai = {
  theme: 'light', displayMode: 'inline', toolResponseMetadata: METADATA,
  requestDisplayMode: async ({mode}) => {
    window.openai.displayMode = mode;
    parent.postMessage({previewMode: mode}, '*');
    return {mode};
  }
};
</script>""".replace("METADATA", json.dumps(result.meta).replace("<", "\\u003c"))
    component = component.replace("<script>", shim + "<script>", 1)
page = """<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prévia local · Card de imagem</title>
<style>
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
body { margin: 0; padding: 24px 18px; background: light-dark(#fff,#171717); }
main { max-width: 560px; margin: auto; }
header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
small { opacity: .55; }
button { padding: 8px 12px; border: 0; border-radius: 20px; cursor: pointer; }
iframe { display: block; width: 100%; height: 580px; border: 0; }
body.expanded iframe { position: fixed; inset: 0; width: 100%; height: 100dvh !important; }
</style><main><header><small>Prévia local · fora do ChatGPT</small>
<button id="theme">Trocar tema</button></header>
<iframe title="Card de imagem" sandbox="allow-scripts" srcdoc="COMPONENT"></iframe></main>
<script>
const frame = document.querySelector('iframe');
const result = PAYLOAD;
const bridge = BRIDGE;
let theme = 'light';
let mode = 'inline';
window.previewRequests = [];
document.documentElement.style.colorScheme = theme;
const send = data => frame.contentWindow.postMessage({jsonrpc:'2.0', ...data}, '*');
function applyMode(next) {
  mode = next;
  document.body.classList.toggle('expanded', mode === 'fullscreen');
  send({method:'ui/notifications/host-context-changed', params:{displayMode:mode}});
}
window.addEventListener('message', event => {
  if (event.source !== frame.contentWindow) return;
  if (event.data?.previewMode) { applyMode(event.data.previewMode); return; }
  if (event.data?.jsonrpc !== '2.0' || bridge === 'legacy') return;
  const m = event.data;
  if (m.method === 'ui/initialize') send({id:m.id, result:{
    protocolVersion:'2026-01-26', hostInfo:{name:'local-preview',version:'1'},
    hostCapabilities:{}, hostContext:{theme, displayMode:mode,
      availableDisplayModes:bridge === 'unsupported' ? ['inline'] : ['inline','fullscreen']}
  }});
  if (m.method === 'ui/notifications/initialized') {
    send({method:'ui/notifications/tool-result',params:result});
  }
  if (m.method === 'ui/request-display-mode') {
    window.previewRequests.push(m.params.mode);
    applyMode(bridge === 'denied' ? 'inline' : m.params.mode);
    send({id:m.id,result:{mode}});
  }
  if (m.method === 'ui/notifications/size-changed' && mode === 'inline') {
    frame.style.height = m.params.height + 'px';
  }
});
document.querySelector('#theme').onclick = () => {
  theme = theme === 'light' ? 'dark' : 'light';
  document.documentElement.style.colorScheme = theme;
  send({method:'ui/notifications/host-context-changed',params:{theme}});
};
</script></html>"""
page = page.replace("COMPONENT", html.escape(component, quote=True))
page = page.replace("PAYLOAD", payload).replace("BRIDGE", json.dumps(args.bridge))
output = Path(".code-harness/image-card-preview/index.html")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(page, encoding="utf-8")
print(output.resolve())
