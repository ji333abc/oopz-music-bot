"use strict";

// Keep the public API response stable while replacing client_search_cp.
function createSearchMiddleware({ getCookie, fetchImpl = fetch }) {
  return async function search(ctx, next) {
    if (ctx.method !== "GET" || ctx.path !== "/getSearchByKey") return next();
    const keyword = String(ctx.query.key || "").trim();
    if (!keyword) {
      ctx.status = 400;
      ctx.body = { error: "请输入搜索关键词" };
      return;
    }
    const limit = Math.max(1, Math.min(100, Number.parseInt(ctx.query.limit, 10) || 10));
    const page = Math.max(1, Number.parseInt(ctx.query.page, 10) || 1);
    try {
      const response = await fetchImpl("https://u.y.qq.com/cgi-bin/musicu.fcg", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Referer: "https://y.qq.com/",
          "User-Agent": "Mozilla/5.0",
          Cookie: String(ctx.get?.("cookie") || getCookie()),
        },
        body: JSON.stringify({
          comm: { ct: 24, cv: 0, format: "json", uin: 0 },
          req_1: {
            module: "music.search.SearchCgiService",
            method: "DoSearchForQQMusicDesktop",
            param: { query: keyword, search_type: 0, num_per_page: limit, page_num: page },
          },
        }),
        signal: AbortSignal.timeout(15000),
      });
      if (!response.ok) {
        ctx.status = 502;
        ctx.body = { error: "QQ音乐搜索上游 HTTP 异常", upstream_status: response.status };
        return;
      }
      const payload = await response.json();
      const result = payload?.req_1;
      const songs = result?.data?.body?.song;
      if (payload?.code !== 0 || result?.code !== 0 || !Array.isArray(songs?.list)) {
        ctx.status = 502;
        ctx.body = {
          error: "QQ音乐搜索上游业务或响应格式异常",
          upstream_code: typeof payload?.code === "number" ? payload.code : null,
          search_code: typeof result?.code === "number" ? result.code : null,
        };
        return;
      }
      ctx.status = 200;
      ctx.body = { response: { code: 0, data: { song: songs } } };
    } catch (error) {
      ctx.status = error?.name === "TimeoutError" ? 504 : 502;
      ctx.body = { error: "QQ音乐搜索上游请求失败，请稍后重试" };
    }
  };
}

module.exports = { createSearchMiddleware };
