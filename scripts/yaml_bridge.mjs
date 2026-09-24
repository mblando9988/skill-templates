// yaml_bridge.mjs - Run Claude Code's real YAML parsers for the Python scripts.
//
// Part of the skill-templates tooling. The Python scripts never parse YAML
// themselves; they send JSON to this bridge on stdin and read JSON back:
//
//   bun --no-install scripts/yaml_bridge.mjs   -> Bun.YAML  (native Claude Code builds)
//   node scripts/yaml_bridge.mjs               -> eemeli/yaml (npm Claude Code builds),
//                                                 pinned in scripts/package.json
//
// Both runtimes parse with default options, exactly as Claude Code calls
// them. Formatting and stringifying use eemeli/yaml's Document API (Node
// only) so comments survive; the Python side verifies every result with
// both parsers before it writes anything.
//
// Request:  {"op": "parse" | "format" | "stringify" | "version", "items": [...]}
// Response: {"ok": true, "runtime": "...", "results": [...]}  or  {"ok": false, "error": "..."}
//
// Security: input arrives only on stdin, nothing is evaluated, and Bun is
// started with --no-install so a missing package is never fetched.

const IS_BUN = typeof Bun !== "undefined";
const MAX_INPUT_BYTES = 8 * 1024 * 1024;

async function readStdin() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_INPUT_BYTES) throw new Error(`request larger than ${MAX_INPUT_BYTES} bytes`);
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

let yamlModule = null;
async function eemeli() {
  if (IS_BUN) throw new Error("eemeli/yaml operations run under Node, not Bun");
  if (!yamlModule) {
    const mod = await import("yaml");
    yamlModule = mod.default ?? mod;
  }
  return yamlModule;
}

// JSON cannot carry NaN, Infinity or undefined; tag them so Python can decode.
function encode(value) {
  if (typeof value === "number" && !Number.isFinite(value)) {
    return { $yaml: Number.isNaN(value) ? "nan" : value > 0 ? "inf" : "-inf" };
  }
  if (value === undefined) return null;
  if (typeof value === "bigint") return { $yaml: "bigint", value: String(value) };
  if (value instanceof Date) return { $yaml: "date", value: value.toISOString() };
  if (Array.isArray(value)) return value.map(encode);
  if (value && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value)) out[key] = encode(value[key]);
    return out;
  }
  return value;
}

function decode(value) {
  if (Array.isArray(value)) return value.map(decode);
  if (value && typeof value === "object") {
    if (typeof value.$yaml === "string" && Object.keys(value).length <= 2) {
      if (value.$yaml === "nan") return NaN;
      if (value.$yaml === "inf") return Infinity;
      if (value.$yaml === "-inf") return -Infinity;
    }
    const out = {};
    for (const key of Object.keys(value)) out[key] = decode(value[key]);
    return out;
  }
  return value;
}

// ---------------------------------------------------------------------------
// parse
// ---------------------------------------------------------------------------

function parseWithBun(text) {
  try {
    const value = Bun.YAML.parse(text);
    return { ok: true, value: encode(value) };
  } catch (err) {
    // Bun reports no source position; the Python side locates the problem.
    return { ok: false, error: { message: String(err && err.message || err) } };
  }
}

function positionsOf(YAML, doc, lineCounter) {
  const out = [];
  const at = (node) => {
    if (!node || !node.range) return null;
    const pos = lineCounter.linePos(node.range[0]);
    return [pos.line, pos.col];
  };
  const walk = (node, path) => {
    if (YAML.isMap(node)) {
      for (const pair of node.items) {
        const key = YAML.isScalar(pair.key) ? String(pair.key.value) : String(pair.key);
        const here = path.concat([key]);
        const pos = at(pair.key);
        if (pos) out.push([here, pos[0], pos[1]]);
        walk(pair.value, here);
      }
    } else if (YAML.isSeq(node)) {
      node.items.forEach((item, index) => {
        const here = path.concat([index]);
        const pos = at(item);
        if (pos) out.push([here, pos[0], pos[1]]);
        walk(item, here);
      });
    }
  };
  walk(doc.contents, []);
  return out;
}

async function parseWithEemeli(text, wantPositions) {
  const YAML = await eemeli();
  const lineCounter = new YAML.LineCounter();
  // Same call Claude Code makes (YAML.parse with default options), expanded
  // so the source positions come back too.
  const doc = YAML.parseDocument(text, { lineCounter });
  if (doc.errors.length) {
    const err = doc.errors[0];
    const pos = err.linePos && err.linePos[0];
    return {
      ok: false,
      error: {
        message: String(err.message).split("\n")[0].replace(/ at line \d+, column \d+:?$/, ""),
        code: err.code,
        line: pos ? pos.line : null,
        col: pos ? pos.col : null,
      },
    };
  }
  const result = { ok: true, value: encode(doc.toJS()) };
  if (doc.warnings.length) {
    result.warnings = doc.warnings.map((w) => String(w.message).split("\n")[0]);
  }
  if (wantPositions) result.positions = positionsOf(YAML, doc, lineCounter);
  return result;
}

// ---------------------------------------------------------------------------
// format / stringify (eemeli/yaml Document API, Node only)
// ---------------------------------------------------------------------------

const STRINGIFY_OPTIONS = {
  indent: 2,
  indentSeq: true,
  lineWidth: 0,
  minContentWidth: 0,
  flowCollectionPadding: false,
  defaultStringType: "PLAIN",
  defaultKeyType: "PLAIN",
  blockQuote: "literal",
};

const NULL_SOURCES = new Set(["~", "null", "Null", "NULL"]);

function samePath(a, b) {
  return a.length === b.length && a.every((part, i) => String(part) === String(b[i]));
}

function startsWith(path, prefix) {
  return prefix.length <= path.length && prefix.every((part, i) => String(part) === String(path[i]));
}

function canonicalize(YAML, doc, spec) {
  const doubleQuote = spec.doubleQuote || [];
  const quoteAll = spec.quoteAll || [];
  const visitScalars = (node, path) => {
    if (!node) return;
    node.spaceBefore = false;
    if (YAML.isMap(node)) {
      for (const pair of node.items) {
        if (pair.key && typeof pair.key === "object") {
          pair.key.spaceBefore = false;
          if (YAML.isScalar(pair.key)) pair.key.type = undefined;
        }
        visitScalars(pair.value, path.concat([YAML.isScalar(pair.key) ? String(pair.key.value) : String(pair.key)]));
      }
    } else if (YAML.isSeq(node)) {
      node.items.forEach((item, index) => {
        // An empty "- " item makes Bun.YAML nest the following keys inside it:
        // always write null items explicitly.
        if (item === null) node.items[index] = item = new YAML.Scalar(null);
        if (YAML.isScalar(item) && item.value === null) item.source = "null";
        visitScalars(item, path.concat([index]));
      });
    } else if (YAML.isScalar(node)) {
      const value = node.value;
      if (typeof value === "boolean" || typeof value === "number") {
        delete node.source;
        if (typeof value === "number" && Number.isInteger(value)) node.format = undefined;
      } else if (value === null) {
        if (NULL_SOURCES.has(node.source)) node.source = "null";
      } else if (typeof value === "string") {
        const forced = doubleQuote.some((p) => samePath(p, path)) || quoteAll.some((p) => startsWith(path, p));
        if (forced) node.type = "QUOTE_DOUBLE";
        else if (!(value.includes("\n") && node.type === "BLOCK_LITERAL")) node.type = undefined;
      }
    }
  };
  visitScalars(doc.contents, []);
}

function applyEdits(YAML, doc, edits) {
  for (const edit of edits || []) {
    const node = doc.getIn(edit.path, true);
    if (YAML.isScalar(node)) {
      node.value = decode(edit.value);
      delete node.source;
      node.type = undefined;
      node.format = undefined;
    } else {
      doc.setIn(edit.path, decode(edit.value));
    }
  }
}

function reorderTopLevel(YAML, doc, keyOrder) {
  if (!keyOrder || !YAML.isMap(doc.contents)) return;
  const rank = new Map(keyOrder.map((key, index) => [key, index]));
  const indexed = doc.contents.items.map((pair, index) => [pair, index]);
  const keyOf = (pair) => (YAML.isScalar(pair.key) ? String(pair.key.value) : String(pair.key));
  indexed.sort((a, b) => {
    const ra = rank.has(keyOf(a[0])) ? rank.get(keyOf(a[0])) : keyOrder.length;
    const rb = rank.has(keyOf(b[0])) ? rank.get(keyOf(b[0])) : keyOrder.length;
    return ra - rb || a[1] - b[1];
  });
  doc.contents.items = indexed.map((entry) => entry[0]);
}

async function formatText(item) {
  const YAML = await eemeli();
  const doc = YAML.parseDocument(item.text);
  if (doc.errors.length) {
    const err = doc.errors[0];
    const pos = err.linePos && err.linePos[0];
    return { ok: false, error: { message: String(err.message).split("\n")[0], code: err.code,
                                 line: pos ? pos.line : null, col: pos ? pos.col : null } };
  }
  if (doc.contents !== null && !YAML.isMap(doc.contents)) {
    return { ok: false, error: { message: "frontmatter is not a YAML mapping" } };
  }
  applyEdits(YAML, doc, item.edits);
  reorderTopLevel(YAML, doc, item.keyOrder);
  canonicalize(YAML, doc, item);
  const text = doc.contents === null ? (doc.commentBefore ? `#${doc.commentBefore.replace(/\n/g, "\n#")}\n` : "")
                                     : doc.toString(STRINGIFY_OPTIONS);
  return { ok: true, text };
}

async function stringifyData(item) {
  const YAML = await eemeli();
  const doc = new YAML.Document(decode(item.data));
  canonicalize(YAML, doc, item);
  return { ok: true, text: doc.toString(STRINGIFY_OPTIONS) };
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

async function each(items, fn) {
  const results = [];
  for (const item of items) {
    try {
      results.push({ id: item.id, ...(await fn(item)) });
    } catch (err) {
      results.push({ id: item.id, ok: false, error: { message: String(err && err.message || err) } });
    }
  }
  return results;
}

async function handle(request) {
  const items = Array.isArray(request.items) ? request.items : [];
  switch (request.op) {
    case "version": {
      if (IS_BUN) return { runtime: "bun", version: Bun.version, parser: "Bun.YAML" };
      const YAML = await eemeli();
      const { createRequire } = await import("node:module");
      const pkg = createRequire(import.meta.url)("yaml/package.json");
      return { runtime: "node", version: process.versions.node, parser: `yaml@${pkg.version}`,
               available: Boolean(YAML.parse) };
    }
    case "parse":
      return { results: await each(items, (item) =>
        IS_BUN ? parseWithBun(item.text) : parseWithEemeli(item.text, request.positions)) };
    case "format":
      return { results: await each(items, formatText) };
    case "stringify":
      return { results: await each(items, stringifyData) };
    default:
      throw new Error(`unknown op ${JSON.stringify(request.op)}`);
  }
}

try {
  const request = JSON.parse(await readStdin());
  const body = await handle(request);
  process.stdout.write(JSON.stringify({ ok: true, runtime: IS_BUN ? "bun" : "node", ...body }));
} catch (err) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(err && err.stack || err) }));
  process.exitCode = 1;
}
