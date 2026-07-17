import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import API from "../api/api";
import { useLocaleStore } from "../i18n/localeStore.js";
import IngestModal from "./IngestModal";

vi.mock("../api/api", () => ({
  default: {
    get: vi.fn(),
  },
}));

function demo(id, filename = `demo-${id}.dem`) {
  return {
    id,
    filename,
    path: `C:\\demos\\${filename}`,
    file_size: 1024 * 1024,
    source: "Local",
    added_at: "2026-07-17T00:00:00Z",
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("IngestModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useLocaleStore.setState({ locale: "zh", effectiveLocale: "zh" });
  });

  test("selecting the current page unions with selections from earlier pages", async () => {
    const firstPage = Array.from({ length: 10 }, (_, index) => demo(index + 1));
    API.get.mockImplementation((_url, { params }) => {
      if (params.offset === 10) {
        return Promise.resolve({ data: { items: [demo(11)], total: 11 } });
      }
      return Promise.resolve({ data: { items: firstPage, total: 11 } });
    });

    render(<IngestModal isOpen onClose={vi.fn()} onIngest={vi.fn()} />);

    await screen.findByText("demo-1.dem");
    fireEvent.click(screen.getByRole("button", { name: /本页累加选择 0\/10/ }));
    expect(screen.getByText("已选 10 / 筛选结果 11")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("demo-11.dem");
    fireEvent.click(screen.getByRole("button", { name: /本页累加选择 0\/1/ }));

    expect(screen.getByText("已选 11 / 筛选结果 11")).toBeTruthy();
  });

  test("pages through discovered results in batches of 1000 when selecting all filtered demos", async () => {
    const firstThousand = Array.from({ length: 1000 }, (_, index) => demo(index + 1));
    API.get.mockImplementation((_url, { params }) => {
      if (params.limit === 1000 && params.offset === 0) {
        return Promise.resolve({ data: { items: firstThousand, total: 1001 } });
      }
      if (params.limit === 1000 && params.offset === 1000) {
        return Promise.resolve({ data: { items: [demo(1001)], total: 1001 } });
      }
      return Promise.resolve({ data: { items: firstThousand.slice(0, 10), total: 1001 } });
    });

    render(<IngestModal isOpen onClose={vi.fn()} onIngest={vi.fn()} />);
    await screen.findByText("demo-1.dem");
    fireEvent.click(screen.getByRole("button", { name: "全选当前筛选结果 (1001)" }));

    await screen.findByText("已选 1001 / 筛选结果 1001");
    expect(API.get).toHaveBeenCalledWith(
      "/demos/discovered",
      { params: { limit: 1000, offset: 0 } },
    );
    expect(API.get).toHaveBeenCalledWith(
      "/demos/discovered",
      { params: { limit: 1000, offset: 1000 } },
    );
  });

  test("selects every filtered result, ingests in chunks of four, and reports partial failures", async () => {
    const all = Array.from({ length: 6 }, (_, index) => demo(index + 1));
    let ingestFinished = false;
    API.get.mockImplementation((_url, { params }) => {
      if (params.limit === 1000) {
        return Promise.resolve({ data: { items: all, total: all.length } });
      }
      if (ingestFinished) {
        return Promise.resolve({ data: { items: [demo(2)], total: 1 } });
      }
      return Promise.resolve({ data: { items: all, total: all.length } });
    });

    const onClose = vi.fn();
    const onIngest = vi
      .fn()
      .mockResolvedValueOnce({
        ingested: 3,
        failed: [{ demo_id: 2, filename: "demo-2.dem", error: "bad header" }],
      })
      .mockResolvedValueOnce({ ingested: 2, failed: [] });
    const onIngestComplete = vi.fn(async () => {
      ingestFinished = true;
    });

    render(
      <IngestModal
        isOpen
        onClose={onClose}
        onIngest={onIngest}
        onIngestComplete={onIngestComplete}
      />,
    );

    await screen.findByText("demo-1.dem");
    fireEvent.click(screen.getByRole("button", { name: "全选当前筛选结果 (6)" }));
    await screen.findByText("已选 6 / 筛选结果 6");
    fireEvent.click(screen.getByRole("button", { name: "确认入库 (6)" }));

    await waitFor(() => expect(onIngest).toHaveBeenCalledTimes(2));
    expect(onIngest.mock.calls[0][0]).toEqual([1, 2, 3, 4]);
    expect(onIngest.mock.calls[1][0]).toEqual([5, 6]);
    await screen.findByText(/bad header/);
    expect(screen.getByText("1 个 Demo 入库失败；请从刷新后的列表重新选择可重试项")).toBeTruthy();
    expect(screen.getByText("已选 0 / 筛选结果 1")).toBeTruthy();
    expect(onIngestComplete).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });

  test("debounces search and ignores a stale response that resolves last", async () => {
    const oldRequest = deferred();
    const newRequest = deferred();
    API.get.mockImplementation((_url, { params }) => {
      if (params.q === "old") return oldRequest.promise;
      if (params.q === "new") return newRequest.promise;
      return Promise.resolve({ data: { items: [demo(1, "initial.dem")], total: 1 } });
    });

    render(<IngestModal isOpen onClose={vi.fn()} onIngest={vi.fn()} />);
    await screen.findByText("initial.dem");
    API.get.mockClear();

    const searchInput = screen.getByRole("textbox", { name: "搜索文件名..." });
    fireEvent.change(searchInput, { target: { value: "o" } });
    expect(screen.queryByText("initial.dem")).toBeNull();
    fireEvent.change(searchInput, { target: { value: "ol" } });
    fireEvent.change(searchInput, { target: { value: "old" } });
    await waitFor(() => {
      expect(API.get).toHaveBeenCalledTimes(1);
      expect(API.get.mock.calls[0][1].params.q).toBe("old");
    });

    fireEvent.change(searchInput, { target: { value: "new" } });
    await waitFor(() => expect(API.get).toHaveBeenCalledTimes(2));
    await act(async () => {
      newRequest.resolve({ data: { items: [demo(3, "new.dem")], total: 1 } });
    });
    await screen.findByText("new.dem");

    await act(async () => {
      oldRequest.resolve({ data: { items: [demo(2, "old.dem")], total: 1 } });
    });
    expect(screen.queryByText("old.dem")).toBeNull();
    expect(screen.getByText("new.dem")).toBeTruthy();
  });

  test("keeps the current results when search changes only by whitespace", async () => {
    API.get.mockResolvedValue({ data: { items: [demo(1, "initial.dem")], total: 1 } });

    render(<IngestModal isOpen onClose={vi.fn()} onIngest={vi.fn()} />);
    await screen.findByText("initial.dem");
    API.get.mockClear();

    fireEvent.change(screen.getByRole("textbox", { name: "搜索文件名..." }), {
      target: { value: " " },
    });

    expect(screen.getByText("initial.dem")).toBeTruthy();
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 350));
    });
    expect(API.get).not.toHaveBeenCalled();
  });

  test("can cancel select-all collection before any IDs have arrived", async () => {
    const selectAllRequest = deferred();
    API.get.mockImplementation((_url, { params }) => {
      if (params.limit === 1000) return selectAllRequest.promise;
      return Promise.resolve({ data: { items: [demo(1)], total: 1 } });
    });

    render(<IngestModal isOpen onClose={vi.fn()} onIngest={vi.fn()} />);
    await screen.findByText("demo-1.dem");
    fireEvent.click(screen.getByRole("button", { name: "全选当前筛选结果 (1)" }));
    fireEvent.click(screen.getByRole("button", { name: "清空" }));

    await act(async () => {
      selectAllRequest.resolve({ data: { items: [demo(1)], total: 1 } });
    });
    expect(screen.getByText("已选 0 / 筛选结果 1")).toBeTruthy();
  });

  test("shows list failures and allows a retry", async () => {
    API.get
      .mockRejectedValueOnce(new Error("database busy"))
      .mockResolvedValueOnce({ data: { items: [demo(1)], total: 1 } });

    render(<IngestModal isOpen onClose={vi.fn()} onIngest={vi.fn()} />);
    await screen.findByText("database busy");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("demo-1.dem");
  });
});
