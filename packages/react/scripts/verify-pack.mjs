import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const temporaryRoot = await mkdtemp(join(tmpdir(), "spoken-usage-react-"));
const consumerRoot = join(temporaryRoot, "consumer");
const cacheRoot = join(temporaryRoot, "npm-cache");

function npm(args, cwd) {
  return execFileSync("npm", args, {
    cwd,
    encoding: "utf8",
    env: { ...process.env, npm_config_cache: cacheRoot },
    stdio: ["ignore", "pipe", "inherit"],
  });
}

try {
  const packed = JSON.parse(npm(["pack", "--json", "--silent", "--pack-destination", temporaryRoot], packageRoot));
  const tarball = join(temporaryRoot, packed[0].filename);
  await mkdir(join(consumerRoot, "src"), { recursive: true });
  await writeFile(join(consumerRoot, "package.json"), JSON.stringify({
    name: "speech-player-package-check",
    private: true,
    type: "module",
    scripts: { build: "tsc --noEmit && vite build" },
    dependencies: {
      "@spoken-usage-retrieval/react": `file:${tarball}`,
      "@types/react": "19.2.18",
      "@types/react-dom": "19.2.7",
      react: "19.2.8",
      "react-dom": "19.2.8",
      typescript: "7.0.2",
      vite: "8.2.2",
    },
  }, null, 2));
  await writeFile(join(consumerRoot, "tsconfig.json"), JSON.stringify({
    compilerOptions: {
      target: "ES2022",
      lib: ["ES2022", "DOM", "DOM.Iterable"],
      strict: true,
      module: "ESNext",
      moduleResolution: "Bundler",
      jsx: "react-jsx",
      isolatedModules: true,
      skipLibCheck: true,
    },
    include: ["src"],
  }, null, 2));
  await writeFile(join(consumerRoot, "index.html"), '<div id="root"></div><script type="module" src="/src/main.tsx"></script>\n');
  await writeFile(join(consumerRoot, "src/vite-env.d.ts"), '/// <reference types="vite/client" />\n');
  await writeFile(join(consumerRoot, "src/styles.css"), '@import "@spoken-usage-retrieval/react/styles.css";\n');
  await writeFile(join(consumerRoot, "src/main.tsx"), `
import React from "react";
import { createRoot } from "react-dom/client";
import { formatClock } from "@spoken-usage-retrieval/react";
import { createSpeechRetrievalClient } from "@spoken-usage-retrieval/react/client";
import { SpeechClipPlayer } from "@spoken-usage-retrieval/react/player";
import type { SpeechClip } from "@spoken-usage-retrieval/react/types";
import "./styles.css";

const clip: SpeechClip = {
  segment_id: "segment-1",
  source_language: "es",
  source_text: "Una frase de ejemplo.",
  sentence_start: 1,
  sentence_end: 3,
  clip_start: 0.7,
  clip_end: 3.3,
  segments: [{ text: "Una frase de ejemplo.", start: 1, end: 3, char_start: 0, char_end: 21 }],
  boundary: { reason: "punctuation", confidence: 1 },
  quality_score: 1,
  analyzer: { name: "unicode", language: "es", package_version: "1", model_version: null, settings: {}, identity: "check" },
  video: {
    video_key: "video-1", provider: "youtube", id: "abc123", url: "https://youtu.be/abc123",
    title: "Example", channel_id: null, channel: "Example channel", source_language: "es",
    varieties: [], speech_style: [], duration: 30, thumbnail: null, track_id: "track-1",
    caption_kind: "manual", caption_language: "es",
  },
  target_language: null,
  target_text: null,
  translation_provenance: null,
  alignment_status: "unavailable",
  alignment_coverage: null,
  alignment_provenance: null,
  alignment_groups: null,
};

createSpeechRetrievalClient({ baseUrl: "/api/v1" });
formatClock(clip.clip_start);
createRoot(document.getElementById("root")!).render(<SpeechClipPlayer clip={clip} />);
`);

  npm(["install", "--ignore-scripts", "--no-audit", "--no-fund"], consumerRoot);
  const reactTree = JSON.parse(npm(["ls", "react", "--all", "--json"], consumerRoot));
  const installedPackage = JSON.parse(await readFile(join(
    consumerRoot,
    "node_modules/@spoken-usage-retrieval/react/package.json",
  ), "utf8"));
  if (installedPackage.dependencies?.react || installedPackage.dependencies?.["react-dom"]) {
    throw new Error("React must be provided only through peerDependencies");
  }
  if (reactTree.dependencies?.react?.version !== "19.2.8") {
    throw new Error("The consumer did not resolve its own React instance");
  }
  npm(["run", "build"], consumerRoot);
  process.stdout.write("Packed consumer type-check and production build passed with one host-owned React.\n");
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}
