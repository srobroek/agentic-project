#!/usr/bin/env node

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

// The argument is the Inlang project directory itself, I18N_PROJECT_DIR, so a project
// nested at apps/web/project.inlang is checked where it is. Message paths in the
// settings resolve against the directory that holds it, as Inlang resolves them.
const inlangDir = path.resolve(process.argv[2] ?? "project.inlang");
const settingsPath = path.join(inlangDir, "settings.json");
const projectDir = path.dirname(inlangDir);

function fail(message) {
  console.error(`locale drift: ${message}`);
  process.exit(1);
}

// oxlint's require-array-sort-compare is an error, so a bare `.sort()` failed the
// scaffold's own `just check`. Code-point order is what `.sort()` meant here, and
// `toSorted` because unicorn/no-array-sort warns on the mutating form.
function byCodePoint(a, b) {
  if (a < b) return -1;
  return a > b ? 1 : 0;
}

function readJson(filePath, label) {
  try {
    return JSON.parse(readFileSync(filePath, "utf8"));
  } catch (error) {
    return fail(`cannot read ${label} (${filePath}): ${error.message}`);
  }
}

function messageKeys(value, label, prefix = "", keys = new Set()) {
  if (typeof value === "string" || Array.isArray(value)) {
    if (!prefix) fail(`${label} must be a message object`);
    keys.add(prefix);
    return keys;
  }

  if (!value || typeof value !== "object") {
    fail(`${label} has unsupported value at ${prefix || "<root>"}`);
  }

  for (const [key, child] of Object.entries(value)) {
    if (key === "$schema") continue;
    const id = prefix ? `${prefix}.${key}` : key;
    messageKeys(child, label, id, keys);
  }
  return keys;
}

const settings = readJson(settingsPath, "Inlang settings");
const { baseLocale, locales = [] } = settings;
if (
  typeof baseLocale !== "string" ||
  !Array.isArray(locales) ||
  locales.length === 0 ||
  locales.some((locale) => typeof locale !== "string") ||
  !locales.includes(baseLocale)
) {
  fail("settings must declare a string baseLocale included in non-empty string locales");
}

const configuredPatterns =
  settings["plugin.inlang.messageFormat"]?.pathPattern ?? "./messages/{locale}.json";
const patterns = Array.isArray(configuredPatterns) ? configuredPatterns : [configuredPatterns];
if (
  patterns.length === 0 ||
  patterns.some(
    (pattern) =>
      typeof pattern !== "string" ||
      (!pattern.includes("{locale}") && !pattern.includes("{languageTag}")),
  )
) {
  fail("message pathPattern must contain {locale} or {languageTag}");
}

function catalogPath(locale, pattern = patterns[0]) {
  const relativePath = pattern.replaceAll("{locale}", locale).replaceAll("{languageTag}", locale);
  return path.resolve(projectDir, relativePath);
}

function keysForLocale(locale) {
  const keys = new Set();
  for (const pattern of patterns) {
    const filePath = catalogPath(locale, pattern);
    messageKeys(readJson(filePath, `catalog ${locale}`), `catalog ${locale}`, "", keys);
  }
  return keys;
}

const baseKeys = keysForLocale(baseLocale);
if (baseKeys.size === 0) fail(`base catalog ${baseLocale} has no messages`);

let drift = false;
for (const locale of locales) {
  // A declared locale with no catalog yet is drift, not a read error: "cannot read
  // catalog de ... ENOENT" told somebody who had just listed `de` nothing to do.
  if (locale !== baseLocale && !existsSync(catalogPath(locale))) {
    drift = true;
    const from = path.relative(process.cwd(), catalogPath(baseLocale));
    const to = path.relative(process.cwd(), catalogPath(locale));
    console.error(`${locale}: no catalog at ${to}; start it from ${from} and translate it`);
    continue;
  }
  const keys = keysForLocale(locale);
  const missing = [...baseKeys].filter((key) => !keys.has(key)).toSorted(byCodePoint);
  const orphaned = [...keys].filter((key) => !baseKeys.has(key)).toSorted(byCodePoint);

  if (missing.length === 0 && orphaned.length === 0) {
    console.log(`${locale}: complete (${keys.size} keys)`);
    continue;
  }

  drift = true;
  console.error(`${locale}: ${missing.length} missing, ${orphaned.length} orphaned`);
  for (const key of missing) console.error(`  missing: ${key}`);
  for (const key of orphaned) console.error(`  orphaned: ${key}`);
}

if (drift) process.exit(1);
