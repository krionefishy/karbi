import { describe, expect, it } from "vitest";

import {
  addFiles,
  clearFinished,
  fileProblem,
  markItems,
  nextToStart,
  queueProgress,
  retryItem,
  setAllOutcomes,
  setItemOutcome,
  submitDrafts,
  type UploadItem,
} from "./queue";

const limits = { max_upload_bytes: 15 * 1024 * 1024, accepted_types: ["image/png", "image/jpeg", "image/webp"] };

const png = (name: string, size = 10) => new File([new Uint8Array(size)], name, { type: "image/png" });

function ids() {
  let n = 0;
  return () => `id-${++n}`;
}

describe("upload queue", () => {
  it("rejects wrong types and oversized files on the spot", () => {
    expect(fileProblem(png("a.png"), limits)).toBe("");
    expect(fileProblem(new File(["x"], "a.gif", { type: "image/gif" }), limits)).toBe(
      "Нужна картинка: PNG, JPEG, WEBP",
    );
    expect(fileProblem(png("big.png", 16 * 1024 * 1024), limits)).toBe("Файл больше 15 МБ");
    expect(fileProblem(new File(["x"], "a.gif", { type: "image/gif" }))).toBe("");

    const items = addFiles([], [new File(["x"], "a.gif", { type: "image/gif" })], "positive", ids(), limits);
    expect(items[0]).toMatchObject({ state: "error", invalid: true });
    expect(retryItem(items, items[0].key)[0].state).toBe("error");
  });

  it("gives every file its own client id and the chosen default outcome", () => {
    const items = addFiles([], [png("a.png"), png("b.png")], "amended", ids(), limits);
    expect(items.map((item) => [item.clientId, item.outcome, item.state])).toEqual([
      ["id-1", "amended", "draft"],
      ["id-2", "amended", "draft"],
    ]);
  });

  it("requires an outcome before sending and clears the error once chosen", () => {
    let items = addFiles([], [png("a.png"), png("b.png")], null, ids(), limits);
    items = setItemOutcome(items, "id-2", "deleted");
    items = submitDrafts(items);
    expect(items.map((item) => [item.state, item.error])).toEqual([
      ["draft", "Выберите исход"],
      ["queued", ""],
    ]);
    items = setItemOutcome(items, "id-1", "positive");
    expect(items[0]).toMatchObject({ outcome: "positive", error: "" });
    expect(submitDrafts(items)[0].state).toBe("queued");
  });

  it("sets one outcome for all files that are not sent yet", () => {
    let items = addFiles([], [png("a.png"), png("b.png"), png("c.png")], null, ids(), limits);
    items = markItems(items, ["id-3"], { state: "done", outcome: "positive" });
    items = setAllOutcomes(items, "amended");
    expect(items.map((item) => item.outcome)).toEqual(["amended", "amended", "positive"]);
  });

  it("starts no more than three uploads at once", () => {
    let items = addFiles(
      [],
      ["a", "b", "c", "d", "e"].map((name) => png(`${name}.png`)),
      "positive",
      ids(),
      limits,
    );
    items = submitDrafts(items);
    expect(nextToStart(items).map((item) => item.key)).toEqual(["id-1", "id-2", "id-3"]);
    items = markItems(items, ["id-1", "id-2"], { state: "uploading" });
    expect(nextToStart(items).map((item) => item.key)).toEqual(["id-3"]);
    items = markItems(items, ["id-3"], { state: "uploading" });
    expect(nextToStart(items)).toEqual([]);
  });

  it("retries with the same client id", () => {
    let items: UploadItem[] = addFiles([], [png("a.png")], "positive", ids(), limits);
    items = markItems(items, ["id-1"], { state: "error", error: "Нет связи с сервером" });
    items = retryItem(items, "id-1");
    expect(items[0]).toMatchObject({ state: "queued", clientId: "id-1", error: "" });
  });

  it("reports progress and clears sent files", () => {
    let items = addFiles([], [png("a.png"), png("b.png"), png("c.png"), png("d.png")], "positive", ids(), limits);
    items = markItems(items, ["id-1"], { state: "done" });
    items = markItems(items, ["id-2"], { state: "uploading" });
    items = markItems(items, ["id-3"], { state: "error" });
    expect(queueProgress(items)).toEqual({ total: 4, done: 1, active: 1, failed: 1, drafts: 1 });
    expect(clearFinished(items).map((item) => item.key)).toEqual(["id-2", "id-3", "id-4"]);
  });
});
