import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library only registers its own cleanup when vitest runs with
// globals enabled. Without this, every render stays in the document and later
// tests query the leftovers of earlier ones.
afterEach(cleanup);

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: () => ({ matches: false, addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false }),
});
