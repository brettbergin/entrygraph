// Resolve the graph palette from Primer CSS custom properties at mount so the
// D3-painted SVG follows the app theme — no hardcoded colors in canvas code.

export interface GraphPalette {
  node: string;
  nodeFocus: string;
  nodeUnexpanded: string;
  stroke: string;
  edge: string;
  edgeHighlight: string;
  text: string;
  textMuted: string;
  halo: string;
}

const FALLBACK: GraphPalette = {
  node: "#161b22",
  nodeFocus: "#1f6feb",
  nodeUnexpanded: "#21262d",
  stroke: "#30363d",
  edge: "#484f58",
  edgeHighlight: "#f85149",
  text: "#e6edf3",
  textMuted: "#7d8590",
  halo: "#0d1117",
};

export function resolvePalette(el: Element): GraphPalette {
  const style = getComputedStyle(el);
  const v = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  return {
    node: v("--bgColor-muted", FALLBACK.node),
    nodeFocus: v("--bgColor-accent-emphasis", FALLBACK.nodeFocus),
    nodeUnexpanded: v("--bgColor-neutral-muted", FALLBACK.nodeUnexpanded),
    stroke: v("--borderColor-default", FALLBACK.stroke),
    edge: v("--fgColor-muted", FALLBACK.edge),
    edgeHighlight: v("--fgColor-danger", FALLBACK.edgeHighlight),
    text: v("--fgColor-default", FALLBACK.text),
    textMuted: v("--fgColor-muted", FALLBACK.textMuted),
    halo: v("--bgColor-default", FALLBACK.halo),
  };
}
