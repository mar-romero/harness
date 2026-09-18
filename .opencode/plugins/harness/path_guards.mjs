import { promises as fs } from "node:fs"
import path from "node:path"

function isReparse(stat) {
  return stat.isSymbolicLink() || (process.platform === "win32" &&
    Boolean(stat.st_file_attributes & 0x400))
}

export function inside(root, candidate) {
  const r = path.resolve(root)
  const c = path.resolve(candidate)
  return c === r || c.startsWith(r + path.sep)
}

export async function insideReal(root, candidate) {
  try {
    const [realRoot, realCandidate] = await Promise.all([
      fs.realpath(root),
      fs.realpath(candidate),
    ])
    return inside(realRoot, realCandidate)
  } catch {
    return false
  }
}

export async function assertNoReparse(root, target) {
  const base = path.resolve(root)
  const absolute = path.resolve(target)
  if (!inside(base, absolute)) throw new Error("Harness: path escapes project root")
  const parts = path.relative(base, absolute).split(path.sep).filter(Boolean)
  let current = base
  for (const part of parts) {
    current = path.join(current, part)
    try {
      const stat = await fs.lstat(current)
      if (isReparse(stat)) throw new Error(`Harness: reparse component is not allowed: ${current}`)
    } catch (error) {
      if (error?.code === "ENOENT") break
      throw error
    }
  }
}
