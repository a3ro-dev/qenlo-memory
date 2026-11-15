// Procedural OBJ models for DitherWorld. Every cube is its own connected piece,
// so the kit's `build` flies them in one by one.

type V = [number, number, number]

function cube(out: { v: V[]; f: number[][] }, c: V, s: number, u: V = [1, 0, 0], w: V = [0, 1, 0]) {
  // a box centred on c with half size s, oriented by the basis (u, w, u×w)
  const n: V = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]]
  const base = out.v.length + 1
  for (const sx of [-1, 1]) for (const sy of [-1, 1]) for (const sz of [-1, 1])
    out.v.push([
      c[0] + s * (sx * u[0] + sy * w[0] + sz * n[0]),
      c[1] + s * (sx * u[1] + sy * w[1] + sz * n[1]),
      c[2] + s * (sx * u[2] + sy * w[2] + sz * n[2]),
    ])
  // vertex index = base + (sx>0)*4 + (sy>0)*2 + (sz>0)
  const q = (a: number, b: number, c2: number, d: number) => out.f.push([base + a, base + b, base + c2, base + d])
  q(0, 1, 3, 2) // -x
  q(4, 6, 7, 5) // +x
  q(0, 4, 5, 1) // -y
  q(2, 3, 7, 6) // +y
  q(0, 2, 6, 4) // -z
  q(1, 5, 7, 3) // +z
}

function toObj(m: { v: V[]; f: number[][] }) {
  return (
    m.v.map((p) => `v ${p[0].toFixed(4)} ${p[1].toFixed(4)} ${p[2].toFixed(4)}`).join("\n") +
    "\n" +
    m.f.map((f) => "f " + f.join(" ")).join("\n")
  )
}

/** The memory: 243 cubes on a sphere, one per memory in the author's own store. */
export function memorySphere(count = 243, r = 1) {
  const m = { v: [] as V[], f: [] as number[][] }
  for (let i = 0; i < count; i++) {
    const y = 1 - (2 * (i + 0.5)) / count
    const rr = Math.sqrt(1 - y * y)
    const t = i * 2.399963229728653
    const p: V = [Math.cos(t) * rr, y, Math.sin(t) * rr]
    // face the cube outwards: u is tangent around y, w completes the frame
    let u: V = [-Math.sin(t), 0, Math.cos(t)]
    const w: V = [p[1] * u[2] - p[2] * u[1], p[2] * u[0] - p[0] * u[2], p[0] * u[1] - p[1] * u[0]]
    const s = 0.055 + 0.02 * ((i * 7919) % 5) / 4
    cube(m, [p[0] * r, p[1] * r, p[2] * r], s, u, w)
  }
  return toObj(m)
}

/** A collection: rows of records, each a row of float columns of different heights. */
export function collection(rows = 10, dims = 16) {
  const m = { v: [] as V[], f: [] as number[][] }
  let seed = 7
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647)
  for (let r = 0; r < rows; r++)
    for (let d = 0; d < dims; d++) {
      const h = 0.06 + rnd() * 0.5
      const x = (d - (dims - 1) / 2) * 0.13
      const z = (r - (rows - 1) / 2) * 0.17
      // a column is a stretched cube: scale y by building it from stacked unit cubes would add pieces,
      // so write it directly
      const base = m.v.length + 1
      const sx = 0.05, sz = 0.06
      for (const ix of [-1, 1]) for (const iy of [0, 1]) for (const iz of [-1, 1]) m.v.push([x + ix * sx, iy * h, z + iz * sz])
      const q = (a: number, b: number, c: number, e: number) => m.f.push([base + a, base + b, base + c, base + e])
      q(0, 1, 3, 2); q(4, 6, 7, 5); q(0, 4, 5, 1); q(2, 3, 7, 6); q(0, 2, 6, 4); q(1, 5, 7, 3)
    }
  return toObj(m)
}
