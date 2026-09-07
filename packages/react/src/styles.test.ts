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
    expect(css).toMatch(/\.sur-player \.sur-player__speed-select\s*\{[^}]*width: 68px;[^}]*font: 11px var\(--sur-player-mono\);/s);
  });

  it("aligns readable timeline text with the left edge of the slider", () => {
    expect(css).toMatch(/\.sur-player__time-row\s*\{[^}]*font: 12px var\(--sur-player-mono\);/s);
    expect(css).toContain(".sur-player__timeline { grid-area: timeline; min-width: 0; }");
  });
});
