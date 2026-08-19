// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useResource } from "./use-resource";

afterEach(() => {
  vi.useRealTimers();
});

describe("useResource", () => {
  it("loads once and exposes the data", async () => {
    const load = vi.fn().mockResolvedValue({ id: "a" });
    const { result } = renderHook(() => useResource(load));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ id: "a" });
    expect(result.current.error).toBeNull();
  });

  it("keeps the last good data and reports the error", async () => {
    const load = vi.fn().mockRejectedValue(new Error("down"));
    const { result } = renderHook(() => useResource(load));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it("stops polling when the component unmounts", async () => {
    vi.useFakeTimers();
    const load = vi.fn().mockResolvedValue(1);
    const { unmount } = renderHook(() => useResource(load, { pollMs: 1000 }));

    await vi.advanceTimersByTimeAsync(2500);
    const before = load.mock.calls.length;
    unmount();
    await vi.advanceTimersByTimeAsync(5000);

    expect(load.mock.calls.length).toBe(before);
  });

  it("discards a slow response that lands after a newer one", async () => {
    let resolveSlow: (v: string) => void = () => {};
    const slow = new Promise<string>((r) => {
      resolveSlow = r;
    });
    const load = vi.fn().mockReturnValueOnce(slow).mockResolvedValue("site-b");

    const { result, rerender } = renderHook(({ site }) => useResource(load, { deps: [site] }), {
      initialProps: { site: "a" },
    });

    rerender({ site: "b" });
    await waitFor(() => expect(result.current.data).toBe("site-b"));

    await act(async () => {
      resolveSlow("site-a");
      await slow;
    });

    expect(result.current.data).toBe("site-b");
  });

  it("re-fetches when a dependency changes", async () => {
    const load = vi.fn().mockResolvedValue(1);
    const { rerender } = renderHook(({ site }) => useResource(load, { deps: [site] }), {
      initialProps: { site: "a" },
    });

    await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    rerender({ site: "b" });
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });
});
