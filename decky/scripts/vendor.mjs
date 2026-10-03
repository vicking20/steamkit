import { cpSync, rmSync } from "node:fs";

rmSync("py_modules/steamkit", { recursive: true, force: true });
cpSync("../steamkit", "py_modules/steamkit", {
  recursive: true,
  filter: (src) => !src.includes("__pycache__"),
});
