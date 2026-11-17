import "./app.css"
import { render } from "solid-js/web"
import { createSignal, onCleanup, onMount, type JSX } from "solid-js"
import {
  Bar,
  BarChart,
  DitherConsole,
  DitherCopyableText,
  DitherThreads,
  DitherWaves,
  DitherDotField,
  DitherFlowField,
  DitherShader,
  DitherTabs,
  DitherTimeline,
  DitherWorld,
  Legend,
  Radar,
  RadarChart,
  Tooltip,
  XAxis,
  YAxis,
  Grid,
  type ConsoleLine,
} from "@dither-kit"
import { collection, memorySphere } from "./models"

// the riso inks: the field, a deep shade of it, bone, fluorescent yellow and pink
const FIELD = "#17215a"
const DEEP = "#0d1440"
const BONE = "#f2eee3"
const YELLOW = "#ffe800"
const PINK = "#ff48b0"
if (new URLSearchParams(location.search).has("og")) document.documentElement.classList.add("og")
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches

/** Mount a Solid island into every [data-island=name], keeping the static fallback for no-JS and screen readers. */
function island(name: string, view: (el: HTMLElement) => JSX.Element) {
  document.querySelectorAll<HTMLElement>(`[data-island="${name}"]`).forEach((el) => {
    el.classList.add("mounted")
    const root = document.createElement("div")
    root.className = "island-root dither-kit"
    el.append(root)
    render(() => view(el), root)
  })
}

/** 0..1 while the element crosses the viewport, for scroll-driven builds. */
function scrollProgress(el: HTMLElement) {
  const [p, setP] = createSignal(reduced ? 1 : 0)
  if (reduced) return p
  let raf = 0
  const read = () => {
    raf = 0
    const r = el.getBoundingClientRect()
    const v = innerHeight
    setP(Math.min(1, Math.max(0, (v * 1.05 - r.top) / (v * 0.8))))
  }
  const on = () => (raf ||= requestAnimationFrame(read))
  addEventListener("scroll", on, { passive: true })
  addEventListener("resize", on)
  read()
  onCleanup(() => {
    removeEventListener("scroll", on)
    removeEventListener("resize", on)
  })
  return p
}

/** Runs once when the element first comes into view. */
function whenSeen(el: HTMLElement, fn: () => void) {
  const io = new IntersectionObserver((es) => {
    if (es.some((e) => e.isIntersecting)) {
      io.disconnect()
      fn()
    }
  }, { threshold: 0.35 })
  io.observe(el)
  onCleanup(() => io.disconnect())
}

// ---- install: tabs + copy

const INSTALL = {
  unix: "curl -fsSL https://raw.githubusercontent.com/a3ro-dev/qenlo-memory/main/install.sh | sh",
  windows: "irm https://raw.githubusercontent.com/a3ro-dev/qenlo-memory/main/install.ps1 | iex",
}

function Install() {
  const [os, setOs] = createSignal<"unix" | "windows">(/Win/.test(navigator.platform) ? "windows" : "unix")
  return (
    <div class="install">
      <DitherTabs
        tabs={[{ value: "unix", label: "mac and linux" }, { value: "windows", label: "windows" }]}
        modelValue={os()}
        onUpdateModelValue={(v) => setOs(v as "unix" | "windows")}
        variant="underline"
        color={YELLOW}
        class="tabs"
      />
      <div class="cmd">
        <code>{INSTALL[os()]}</code>
        <DitherCopyableText value={INSTALL[os()]} label="copy the install command">
          {(s) => <span class="copy">{s.copied ? "copied" : "copy"}</span>}
        </DitherCopyableText>
      </div>
    </div>
  )
}
island("install-hero", () => <Install />)
island("install-main", () => <Install />)

// ---- the memory sphere: builds on load, then leans away from the pointer

island("hero-world", () => {
  const [build, setBuild] = createSignal(reduced ? 1 : 0)
  onMount(() => {
    if (reduced) return
    const t0 = performance.now()
    const step = (now: number) => {
      const t = Math.min(1, (now - t0) / 3200)
      setBuild(1 - Math.pow(1 - t, 3))
      if (t < 1) requestAnimationFrame(step)
    }
    requestAnimationFrame(step)
  })
  return (
    <DitherWorld
      source={memorySphere()}
      format="obj"
      colors={[DEEP, "#3a4fb8", BONE, YELLOW]}
      heat={PINK}
      build={build()}
      nudge
      autoRotate={reduced ? 0 : 9}
      yaw={30}
      pitch={18}
      zoom={1.05}
      cell={3}
      shade={0.22}
      bloom="low"
      label="the memory sphere"
      class="h-full w-full"
    />
  )
})

// ---- the story consoles, typed out when they come into view

function typed(el: HTMLElement, script: ConsoleLine[], delay = 0) {
  // the whole exchange shows from the start; once it scrolls into view it types itself out again
  const [lines, setLines] = createSignal<ConsoleLine[]>(script)
  if (!reduced)
    whenSeen(el, () => {
      setLines([])
      script.forEach((l, i) => setTimeout(() => setLines((ls) => [...ls, l]), delay + 650 * i))
    })
  return lines
}

island("console-codex", (el) => {
  const lines = typed(el, [
    { text: '$ qenlo-memory remember "release: tag, run sdk-release.yml, verify SHA256SUMS" --kind procedural' },
    { text: "remembered #41", level: "success" },
  ])
  return <DitherConsole title="codex" lines={lines()} class="h-44" />
})

island("console-claude", (el) => {
  const lines = typed(
    el,
    [
      { text: '$ qenlo-memory recall "how do we release qenlo"' },
      { text: "#41 procedural | by codex | 2026-11-15 15:12 | qenlo", level: "success" },
      { text: "release: tag, run sdk-release.yml, verify SHA256SUMS" },
    ],
    1600,
  )
  return <DitherConsole title="claude code" lines={lines()} class="h-44" />
})

island("console-conflict", (el) => {
  const lines = typed(
    el,
    [
      { text: '$ qenlo-memory remember "the daemon listens on 7438"' },
      { text: "remembered #52", level: "success" },
      { text: "CONFLICT: #52 seems to contradict these; they stay live, marked possibly stale.", level: "warn" },
      { text: "#12 semantic | by codex | 2026-10-30 | the daemon listens on 7437" },
      { text: '$ qenlo-memory recall "daemon port"' },
      { text: "#12 semantic | by codex | 2026-10-30 | the daemon listens on 7437 | possibly stale: newer #52 disagrees", level: "warn" },
    ],
    400,
  )
  return <DitherConsole title="qenlo-memory" lines={lines()} class="h-56" />
})

// ---- recall: three fields adding up, the pointer is the query

const RECALL_GLSL = `
void mainImage(out vec4 o, in vec2 f) {
  vec2 uv = (f - 0.5 * iResolution.xy) / iResolution.y;
  vec2 q = iMouse.z > 0.0 || iMouse.x > 0.0 ? (iMouse.xy - 0.5 * iResolution.xy) / iResolution.y : vec2(0.0);
  float t = iTime * 0.3;
  vec2 a = q + vec2(-0.34 + 0.05 * sin(t), 0.06 * cos(t * 1.3));
  vec2 b = q + vec2(0.30 + 0.05 * cos(t * 0.9), -0.04 * sin(t));
  vec2 c = q + vec2(0.02 * sin(t * 1.7), 0.26 + 0.03 * cos(t));
  float m = 0.5 * exp(-dot(uv - a, uv - a) * 9.0)
          + 0.5 * exp(-dot(uv - b, uv - b) * 9.0)
          + 0.25 * exp(-dot(uv - c, uv - c) * 12.0);
  m += 0.06 * sin(uv.x * 40.0 + t) * sin(uv.y * 40.0 - t);
  o = vec4(vec3(clamp(m, 0.0, 1.0)), 1.0);
}`

island("recall-shader", () => (
  <DitherShader
    source={RECALL_GLSL}
    colors={[FIELD, "#3a4fb8", PINK, YELLOW]}
    levels={4}
    cell={4}
    speed={reduced ? 0 : 1}
    paused={reduced}
    label="recall as three adding fields"
    class="h-full w-full"
  />
))

// ---- a fact that changed

island("timeline", () => (
  <DitherTimeline
    items={[
      { title: "#12 the daemon listens on 7437", time: "by codex, oct 30", body: "true at the time", color: "grey" },
      { title: "#52 the daemon listens on 7438", time: "by claude code, nov 4", body: "saved with --replaces 12, so #12 leaves search", color: YELLOW },
      { title: "history 12", time: "any time later", body: "#12, replaced by #52. kept, never deleted", color: PINK },
    ]}
    dotSize={14}
  />
))

// ---- charts

island("chart-edgemem", () => {
  const rows = [
    { metric: "R@3", qenlo: 82.77, edgemem: 81.49, memgas: 78.51 },
    { metric: "NDCG@3", qenlo: 91.44, edgemem: 90.49, memgas: 86.83 },
    { metric: "R@10", qenlo: 94.89, edgemem: 93.83, memgas: 94.47 },
    { metric: "NDCG@10", qenlo: 93.81, edgemem: 92.8, memgas: 89.96 },
  ]
  const config = {
    qenlo: { label: "qenlo-memory 0.4", color: YELLOW },
    edgemem: { label: "EdgeMem", color: "#8c98d8" },
    memgas: { label: "MemGAS", color: "#4a5aa8" },
  }
  return (
    <div class="h-72">
      <BarChart data={rows} config={config} bloom="low">
        <Grid horizontal />
        <XAxis dataKey="metric" />
        <YAxis tickCount={4} />
        <Bar dataKey="qenlo" />
        <Bar dataKey="edgemem" variant="hatched" />
        <Bar dataKey="memgas" variant="dotted" />
        <Legend align="right" />
        <Tooltip labelKey="metric" />
      </BarChart>
    </div>
  )
})

island("chart-mem0", () => {
  const pairs = [
    { name: "recall@5", note: "higher is better", q: 58.1, m: 54.3, unit: "" },
    { name: "search p50", note: "ms, lower is better", q: 34, m: 50, unit: " ms" },
    { name: "peak ram", note: "MB, lower is better", q: 47, m: 154, unit: " MB" },
    { name: "installed", note: "MB, lower is better", q: 52, m: 1000, unit: " MB" },
  ]
  const config = { q: { label: "qenlo-memory", color: YELLOW }, m: { label: "mem0", color: "#8c98d8" } }
  return (
    <div class="quad-grid">
      {pairs.map((p) => (
        <div class="quad-cell">
          <p class="quad-name">{p.name}</p>
          <p class="quad-note">{p.note}</p>
          <div class="h-40">
            <BarChart data={[{ k: p.name, q: p.q, m: p.m }]} config={config}>
              <Bar dataKey="q" />
              <Bar dataKey="m" variant="hatched" />
              <Tooltip labelKey="k" />
            </BarChart>
          </div>
          <p class="quad-vals"><b>{p.q}{p.unit}</b> vs {p.m === 1000 ? "1.0 GB" : p.m + p.unit}</p>
        </div>
      ))}
    </div>
  )
})

island("chart-qa", () => {
  const rows = [
    { axis: "assistant", v: 98.2 },
    { axis: "user", v: 95.7 },
    { axis: "update", v: 85.9 },
    { axis: "preference", v: 70.0 },
    { axis: "temporal", v: 62.4 },
    { axis: "multi-session", v: 48.9 },
  ]
  return (
    <div class="h-80">
      <RadarChart data={rows} config={{ v: { label: "accuracy %", color: YELLOW } }} nameKey="axis">
        <Radar dataKey="v" />
      </RadarChart>
    </div>
  )
})

island("chart-qenlo", () => {
  const rows = [{ k: "exact search p95, ms", qenlo: 12.9, milvus: 76.2, lance: 242.3 }]
  const config = {
    qenlo: { label: "qenlo", color: YELLOW },
    milvus: { label: "Milvus Lite 3.0", color: "#8c98d8" },
    lance: { label: "LanceDB 0.38", color: "#4a5aa8" },
  }
  return (
    <div class="h-64">
      <BarChart data={rows} config={config} bloom="low">
        <Grid horizontal />
        <YAxis tickCount={4} />
        <Bar dataKey="qenlo" />
        <Bar dataKey="milvus" variant="hatched" />
        <Bar dataKey="lance" variant="dotted" />
        <Legend align="right" />
        <Tooltip labelKey="k" />
      </BarChart>
    </div>
  )
})

// ---- qenloDB: a collection that fills as you scroll

island("qenlo-world", (el) => {
  const p = scrollProgress(el)
  return (
    <DitherWorld
      source={collection()}
      format="obj"
      colors={[DEEP, "#3a4fb8", BONE, YELLOW]}
      heat={PINK}
      build={p()}
      nudge
      autoRotate={0}
      yaw={-28}
      pitch={30}
      zoom={1.1}
      cell={3}
      label="a qenlo collection"
      class="h-full w-full"
    />
  )
})

// ---- riso plates between sections, and the footer's print. agents as threads, memories as a field.

island("plate-1", () => (
  <DitherThreads colors={[YELLOW, PINK]} count={25} amplitude={0.5} distortion={1.6} speed={0.35} paused={reduced} class="h-full w-full" />
))
island("plate-2", () => (
  <DitherWaves colors={[PINK, BONE]} count={12} amplitude={0.45} speed={0.4} paused={reduced} class="h-full w-full" />
))
island("plate-3", () => (
  <DitherDotField colors={[YELLOW, "#3a4fb8"]} gap={22} speed={0.35} paused={reduced} class="h-full w-full" />
))
island("foot-art", () => (
  <DitherFlowField colors={[YELLOW, PINK, "#3a4fb8"]} count={1800} speed={0.5} paused={reduced} class="h-full w-full" />
))
