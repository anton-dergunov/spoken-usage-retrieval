import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("src/styles.css", "utf8");

function luminance(hex: string): number {
  const channels = hex.match(/[a-f\d]{2}/gi)?.map((value) => {
    const channel = Number.parseInt(value, 16) / 255;
    return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
  });
  if (!channels || channels.length !== 3) throw new Error(`Invalid color: ${hex}`);
  return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2];
}

function contrast(left: string, right: string): number {
  const [lighter, darker] = [luminance(left), luminance(right)].sort((a, b) => b - a);
  return (lighter + .05) / (darker + .05);
}

describe("package styles", () => {
  it("limits motion overrides to the player under reduced-motion preferences", () => {
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain(".sur-player *::before");
    expect(css).not.toMatch(/(^|\n)\s*\*,\s*\*::before/);
  });

  it("provides AA contrast for default normal text and progressive text", () => {
    expect(contrast("#132220", "#fbfcfc")).toBeGreaterThanOrEqual(4.5);
    expect(contrast("#586b66", "#fbfcfc")).toBeGreaterThanOrEqual(4.5);
    expect(contrast("#657772", "#fbfcfc")).toBeGreaterThanOrEqual(4.5);
    expect(contrast("#69420f", "#efe3ce")).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps retry controls and their repeat icons square", () => {
    expect(css).toMatch(/\.sur-player__translation-retry\s*\{[^}]*width: 24px;[^}]*height: 24px;[^}]*padding: 0;/s);
    expect(css).toMatch(/\.sur-player__translation-retry \.sur-player__repeat-icon\s*\{ width: 15px; height: 15px; flex: 0 0 auto; \}/);
    expect(css).toContain(".sur-player .sur-player__repeat-icon");
  });

  it("gives the native speed selector a specific compact font and enough width", () => {
    expect(css).toMatch(/\.sur-player \.sur-player__speed-select\s*\{[^}]*width: 68px;[^}]*margin-left: 12px;[^}]*font: 11px var\(--sur-player-mono\);/s);
  });

  it("carries fewer controls on a phone rather than folding the same ones onto more rows", () => {
    const phone = css.slice(css.indexOf("@media (max-width: 540px)"));
    // The presets go and the select stays: it already offers those four rates and four more, in the
    // width of one of them. The base rule's margin has nothing left to separate it from.
    expect(phone).toContain(".sur-player__transport .sur-player__speed-preset { display: none; }");
    expect(phone).toContain(".sur-player .sur-player__speed-select { margin-left: 0; }");
    // The replay goes too, so the slider gets the width. `r` and dragging back still reach it.
    expect(phone).toContain(".sur-player__transport-secondary { display: none; }");
    // And so the transport is one row, not two, with nothing left to wrap.
    expect(phone).toContain(".sur-player__transport-footer { flex-wrap: nowrap; }");
    expect(phone).toMatch(/grid-template-areas:\s*\n\s*"primary timeline"\s*\n\s*"primary footer";/);
  });

  it("keeps the source link beside the channel at every width", () => {
    const phone = css.slice(css.indexOf("@media (max-width: 540px)"));
    // It used to take a line of its own on a phone — a whole row spent on six words.
    expect(phone).not.toMatch(/\.sur-player__source-line a\s*\{[^}]*width: 100%/);
    expect(phone).toContain(".sur-player__source-line a { flex: 0 0 auto; }");
    expect(phone).toMatch(/\.sur-player__source-line\s*\{[^}]*flex-wrap: nowrap;/s);
    // Which only works if the channel beside it is allowed to give way.
    expect(phone).toMatch(/text-overflow: ellipsis;/);
  });

  it("sizes the passage and its translation to fit a phone together", () => {
    const phone = css.slice(css.indexOf("@media (max-width: 540px)"));
    expect(phone).toContain(".sur-player__source-text { font-size: 17px; }");
    expect(phone).toContain(".sur-player__target-text { font-size: 15px; }");
    // Wider screens keep the sizes they had; only the narrow case was ever cramped.
    expect(css).toContain("font: 400 clamp(18px, 2vw, 23px)/1.52 var(--sur-player-serif)");
  });

  it("lets an alignment token inherit the spacing of the line it replaces", () => {
    // A token is a <button>, and every UA stylesheet gives one `letter-spacing: normal` and
    // `word-spacing: normal` — which `font: inherit` does not override. The source line sets
    // `letter-spacing: -.015em`, so without these the whole passage was re-tracked and re-wrapped
    // the moment the alignment graph arrived: the same characters, visibly re-spaced under the
    // reader. jsdom does no layout, so this can only be asserted on the stylesheet text.
    const token = css.slice(css.indexOf(".sur-player__alignment-token {"));
    const rule = token.slice(0, token.indexOf("}"));
    expect(rule).toContain("letter-spacing: inherit;");
    expect(rule).toContain("word-spacing: inherit;");
    expect(css).toMatch(/\.sur-player__source-text\s*\{[^}]*letter-spacing: -\.015em;/s);
  });

  it("gives a token no horizontal geometry of its own", () => {
    // The highlight keeps its padding; the margin cancels it exactly, so a line is the same width
    // whether or not its words have become tokens. Both halves have to move together, which is why
    // the numbers are asserted as a pair rather than individually.
    const token = css.slice(css.indexOf(".sur-player__alignment-token {"));
    expect(token.slice(0, token.indexOf("}"))).toMatch(/padding: \.04em \.06em;[\s\S]*margin: 0 -\.06em;/);
    const active = css.slice(css.indexOf(".sur-player__target-fragment--active {"));
    expect(active.slice(0, active.indexOf("}"))).toMatch(/padding: \.04em \.12em;[\s\S]*margin: 0 -\.12em;/);
  });

  it("aligns readable timeline text with the left edge of the slider", () => {
    expect(css).toMatch(/\.sur-player__time-row\s*\{[^}]*font: 12px var\(--sur-player-mono\);/s);
    expect(css).toContain(".sur-player__timeline { grid-area: timeline; min-width: 0; }");
  });
});
